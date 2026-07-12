from __future__ import annotations

from typing import Any

from knowledge_base.arango import ArangoError
from knowledge_base.constants import VECTOR_DIMENSION
from knowledge_base.embeddings import EmbeddingProvider
from knowledge_base.repository import KnowledgeRepository

# GR-5: local/global GraphRAG search. Global pulls a candidate document pool (via hybrid), maps it to
# communities, and returns the strongest communities; local expands the seed documents into their
# connecting entities, similarity-neighbours, and communities.
_GLOBAL_CANDIDATE_POOL = 50
_GLOBAL_COMMUNITY_LIMIT = 5
_LOCAL_RELATED_LIMIT = 10
_LOCAL_ENTITY_LIMIT = 15


def _hybrid_search(*args, **kwargs):
    from knowledge_base.retrieval import hybrid_search

    return hybrid_search(*args, **kwargs)


def _related_documents(*args, **kwargs):
    from knowledge_base.retrieval import _related_documents as related_documents

    return related_documents(*args, **kwargs)


def local_search(
    repository: KnowledgeRepository,
    query: str,
    *,
    limit: int = 10,
    dimension: int = VECTOR_DIMENSION,
    source_key: str | None = None,
    provider: EmbeddingProvider | None = None,
    min_similarity: float = 0.0,
) -> dict[str, Any]:
    """GR-5 local GraphRAG search: assemble the local subgraph around a query's strongest documents.

    Retrieves seed documents with hybrid search, then expands along the knowledge graph to return the
    entities (topics/authors/works) that connect them, the documents they are similarity-linked to
    (item_related_to_item, GR-3), and the communities (GR-4) they belong to — a focused, cited local
    context rather than a flat ranked list.
    """
    context: dict[str, Any] = {
        "query": query,
        "mode": "graphrag-local",
        "status": "ok",
        "degraded_components": [],
        "seeds": [],
        "entities": [],
        "related_documents": [],
        "communities": [],
    }
    # The initial retrieval must honour the never-throw contract too: a DB/vector failure here yields a
    # degraded result, not an escaping ArangoError (PR #32 review).
    try:
        hybrid = _hybrid_search(
            repository,
            query,
            limit=limit,
            dimension=dimension,
            source_key=source_key,
            provider=provider,
            min_similarity=min_similarity,
        )
    except ArangoError:
        _mark_degraded(context, "retrieval")
        return context
    context["status"] = hybrid["status"]
    context["degraded_components"] = list(hybrid.get("degraded_components", []))
    context["seeds"] = hybrid["results"]
    seed_keys = [row["document_key"] for row in hybrid["results"]]
    if not seed_keys:
        return context
    try:
        context["entities"] = _entities_for_documents(repository, seed_keys, limit=_LOCAL_ENTITY_LIMIT)
        context["related_documents"] = _related_documents(
            repository, seed_keys, limit=_LOCAL_RELATED_LIMIT, source_key=source_key
        )
        context["communities"] = _communities_for_documents(repository, seed_keys)
    except ArangoError:
        _mark_degraded(context, "graph")
    return context

def global_search(
    repository: KnowledgeRepository,
    query: str,
    *,
    limit: int = 10,
    community_limit: int = _GLOBAL_COMMUNITY_LIMIT,
    dimension: int = VECTOR_DIMENSION,
    source_key: str | None = None,
    provider: EmbeddingProvider | None = None,
    min_similarity: float = 0.0,
) -> dict[str, Any]:
    """GR-5 global GraphRAG search: answer at the corpus level over community summaries (GR-4).

    Retrieves a candidate pool of documents with hybrid search, maps each to its community, and ranks
    communities by the aggregated relevance of their matching documents. Returns the top communities
    with their extractive summaries and the member documents that matched (citations with provenance)
    — the map/reduce shape of GraphRAG global search, grounded in retrieval evidence rather than an LLM
    pass over every summary. `limit` bounds the documents shown per community.
    """
    context: dict[str, Any] = {
        "query": query,
        "mode": "graphrag-global",
        "status": "ok",
        "degraded_components": [],
        "communities": [],
    }
    # A larger candidate pool than `limit` is retrieved so community ranking sees enough evidence, but
    # the initial retrieval still honours the never-throw contract (PR #32 review).
    try:
        hybrid = _hybrid_search(
            repository,
            query,
            limit=max(limit, _GLOBAL_CANDIDATE_POOL),
            dimension=dimension,
            source_key=source_key,
            provider=provider,
            min_similarity=min_similarity,
        )
    except ArangoError:
        _mark_degraded(context, "retrieval")
        return context
    context["status"] = hybrid["status"]
    context["degraded_components"] = list(hybrid.get("degraded_components", []))
    candidates = hybrid["results"]
    if not candidates:
        return context
    try:
        membership = _community_membership(repository, [row["document_key"] for row in candidates])
        communities = _communities_by_id(repository, sorted({m["community"] for m in membership}))
    except ArangoError:
        _mark_degraded(context, "graph")
        return context
    # `limit` is the CLI "documents shown per community" flag, so it bounds citations directly.
    context["communities"] = _aggregate_community_scores(
        candidates,
        membership,
        communities,
        community_limit=community_limit,
        docs_per_community=limit,
    )
    return context

def _mark_degraded(context: dict[str, Any], component: str) -> None:
    context["status"] = "degraded"
    context.setdefault("degraded_components", [])
    if component not in context["degraded_components"]:
        context["degraded_components"].append(component)

def _entities_for_documents(repository: KnowledgeRepository, document_keys: list[str], *, limit: int) -> list[dict[str, Any]]:
    """Entities (topics/authors/works) linking the given documents, ranked by how many mention them."""
    unique_keys = list(dict.fromkeys(document_keys))
    if not unique_keys:
        return []
    return repository.client.aql(
        """
        LET mentions = (
          FOR doc_key IN @document_keys
            LET from_ids = PUSH(
              (FOR c IN chunks FILTER c.document_key == doc_key RETURN c._id),
              CONCAT("documents/", doc_key)
            )
            FOR pair IN UNION_DISTINCT(
                (FOR e IN document_mentions_topic FILTER e._from IN from_ids RETURN {id: e._to, kind: "topic"}),
                (FOR e IN document_mentions_author FILTER e._from IN from_ids RETURN {id: e._to, kind: "author"}),
                (FOR e IN document_references_work FILTER e._from IN from_ids RETURN {id: e._to, kind: "work"}))
              RETURN pair
        )
        FOR m IN mentions
          COLLECT id = m.id, kind = m.kind WITH COUNT INTO documents
          LET node = DOCUMENT(id)
          LET label = node.label != null ? node.label : (node.title != null ? node.title : id)
          SORT documents DESC, label ASC
          LIMIT @limit
          RETURN { id: id, kind: kind, label: label, documents: documents }
        """,
        {"document_keys": unique_keys, "limit": limit},
    )

def _communities_for_documents(repository: KnowledgeRepository, document_keys: list[str]) -> list[dict[str, Any]]:
    """Communities (GR-4) the given documents belong to, with how many of them fall in each."""
    unique_keys = list(dict.fromkeys(document_keys))
    if not unique_keys:
        return []
    return repository.client.aql(
        """
        LET memberships = (
          FOR doc_key IN @document_keys
            FOR e IN document_in_community FILTER e._from == CONCAT("documents/", doc_key)
              RETURN e._to
        )
        FOR community_id IN memberships
          COLLECT cid = community_id WITH COUNT INTO seed_members
          LET c = DOCUMENT(cid)
          FILTER c != null
          SORT seed_members DESC, cid ASC
          RETURN {
            community_key: c._key,
            size: c.size,
            summary: c.summary,
            top_topics: c.top_topics,
            seed_members: seed_members
          }
        """,
        {"document_keys": unique_keys},
    )

def _community_membership(repository: KnowledgeRepository, document_keys: list[str]) -> list[dict[str, str]]:
    """Map each document to the community it belongs to (documents not in a community are omitted)."""
    unique_keys = list(dict.fromkeys(document_keys))
    if not unique_keys:
        return []
    return repository.client.aql(
        """
        FOR doc_key IN @document_keys
          FOR e IN document_in_community FILTER e._from == CONCAT("documents/", doc_key)
            RETURN { doc: doc_key, community: e._to }
        """,
        {"document_keys": unique_keys},
    )

def _communities_by_id(repository: KnowledgeRepository, community_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not community_ids:
        return {}
    rows = repository.client.aql(
        "FOR id IN @ids LET c = DOCUMENT(id) FILTER c != null RETURN { id: id, community: c }",
        {"ids": community_ids},
    )
    return {row["id"]: row["community"] for row in rows}

def _aggregate_community_scores(
    candidates: list[dict[str, Any]],
    membership: list[dict[str, str]],
    communities: dict[str, dict[str, Any]],
    *,
    community_limit: int,
    docs_per_community: int,
) -> list[dict[str, Any]]:
    """Rank communities by the summed relevance of their matched candidate documents (pure).

    Each community's score is the sum of its member candidates' hybrid scores, so a community with
    several strong matches outranks one with a single hit — the corpus-level relevance signal. Only
    the top `docs_per_community` matched documents are returned as citations.
    """
    scores = {row["document_key"]: row for row in candidates}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in membership:
        row = scores.get(entry["doc"])
        if row is not None:
            grouped.setdefault(entry["community"], []).append(row)

    ranked: list[dict[str, Any]] = []
    for community_id, rows in grouped.items():
        community = communities.get(community_id)
        if community is None:
            continue
        members = sorted(rows, key=lambda item: item["score"], reverse=True)
        ranked.append(
            {
                "community_key": community["_key"],
                "size": community.get("size"),
                "summary": community.get("summary"),
                "top_topics": community.get("top_topics", []),
                "score": round(sum(row["score"] for row in members), 6),
                "matched_documents": len(members),
                "documents": [
                    {
                        "document_key": row["document_key"],
                        "title": row.get("title"),
                        "score": row["score"],
                        "provenance": row.get("provenance"),
                    }
                    for row in members[:docs_per_community]
                ],
            }
        )
    # Score descending; ties broken by community_key ascending (consistent with the sibling AQL
    # helpers' `... ASC` tie-breaks). Two stable sorts: secondary key first, then primary.
    ranked.sort(key=lambda item: item["community_key"])
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked[:community_limit]

