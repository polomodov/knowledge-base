from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any

from knowledge_base.constants import RELATED_EDGE_METHOD
from knowledge_base.repository import KnowledgeRepository

__all__ = [
    "community_rollups",
    "document_similarity_edges",
    "document_similarity_projection",
    "document_topic_memberships",
    "ego_graph",
    "timeline_buckets",
    "topic_cooccurrence",
]

_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])")


def document_topic_memberships(
    repository: KnowledgeRepository,
    *,
    include_drafts: bool = False,
) -> list[dict[str, Any]]:
    """Return the canonical distinct-document topic membership.

    Mentions attached both to a document and to any of its chunks collapse to one
    ``(document_key, topic_key)`` row. No confidence or method filter is applied:
    only dangling endpoints and documents outside the requested publication scope
    are omitted.
    """
    rows = repository.client.aql(
        """
        /* viz:topic_mentions */
        FOR edge IN document_mentions_topic
          LET endpoint = DOCUMENT(edge._from)
          FILTER endpoint != null
          LET document_key = HAS(endpoint, "document_key") ? endpoint.document_key : endpoint._key
          COLLECT distinct_document_key = document_key, topic_id = edge._to
          LET topic = DOCUMENT(topic_id)
          LET document = DOCUMENT("documents", distinct_document_key)
          FILTER document != null AND topic != null
          FILTER (@include_drafts AND document.status IN ["published", "draft"])
            OR (NOT @include_drafts AND document.status == "published")
          SORT distinct_document_key ASC, topic._key ASC
          RETURN {
            document: KEEP(document, ["_key", "title", "source_key", "published_at", "url", "status"]),
            topic: KEEP(topic, ["_key", "label"])
          }
        """,
        {"include_drafts": include_drafts},
        batch_size=25_000,
    )
    memberships: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        document = row.get("document")
        topic = row.get("topic")
        if not isinstance(document, dict) or not isinstance(topic, dict):
            continue
        if not _document_is_visible(document, include_drafts=include_drafts):
            continue
        document_key = _key(document)
        topic_key = _key(topic)
        if document_key is None or topic_key is None:
            continue
        memberships.setdefault(
            (document_key, topic_key),
            {
                "document_key": document_key,
                "topic_key": topic_key,
                "topic_label": str(topic.get("label") or topic_key),
                "source_key": document.get("source_key"),
                "published_at": document.get("published_at"),
                "status": document.get("status"),
            },
        )
    return [memberships[key] for key in sorted(memberships)]


def document_similarity_edges(
    repository: KnowledgeRepository,
    *,
    top_k: int | None = 10,
    include_drafts: bool = False,
) -> list[dict[str, Any]]:
    """Fold chunk similarity to undirected document edges.

    Each document pair keeps ``MAX(weight)`` and the number of contributing chunk
    pairs. With ``top_k`` set, the result is the union of every document's top-K
    neighbours, ranked by the unrounded maximum weight and then neighbour key.
    ``top_k=None`` returns the complete fold for graph export.
    """
    return document_similarity_projection(
        repository,
        top_k=top_k,
        include_drafts=include_drafts,
    )["edges"]


def document_similarity_projection(
    repository: KnowledgeRepository,
    *,
    top_k: int | None = 10,
    include_drafts: bool = False,
) -> dict[str, Any]:
    """Return the public edge union and canonical per-document neighbour ranks.

    Rankings use the unrounded MAX weight and deterministic neighbour-key tie break. Keeping them
    beside the rounded display edges prevents the offline UI from reconstructing a different top-K
    from the undirected union.
    """
    if top_k is not None and top_k < 1:
        raise ValueError("top_k must be at least 1 or None")
    pairs = _document_similarity_pairs(repository, include_drafts=include_drafts)
    incident: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in pairs:
        incident[edge["source"]].append(edge)
        incident[edge["target"]].append(edge)
    ranked_neighbors: dict[str, list[str]] = {}
    ranked_edges: dict[str, list[dict[str, Any]]] = {}
    for document_key, edges in sorted(incident.items()):
        ranked = sorted(
            edges,
            key=lambda edge: (-edge["_weight"], _other_endpoint(edge, document_key)),
        )
        if top_k is not None:
            ranked = ranked[:top_k]
        ranked_edges[document_key] = ranked
        ranked_neighbors[document_key] = [_other_endpoint(edge, document_key) for edge in ranked]

    selected: set[tuple[str, str]]
    if top_k is None:
        selected = {(edge["source"], edge["target"]) for edge in pairs}
    else:
        selected = set()
        for edges in ranked_edges.values():
            selected.update((edge["source"], edge["target"]) for edge in edges)
    return {
        "edges": [_public_similarity_edge(edge) for edge in pairs if (edge["source"], edge["target"]) in selected],
        "neighbors": ranked_neighbors,
    }


def topic_cooccurrence(
    repository: KnowledgeRepository,
    *,
    min_documents: int = 2,
    include_drafts: bool = False,
) -> list[dict[str, Any]]:
    """Count pairs of topics co-mentioned by distinct documents."""
    if min_documents < 1:
        raise ValueError("min_documents must be at least 1")
    memberships = document_topic_memberships(repository, include_drafts=include_drafts)
    topics_by_document: dict[str, set[str]] = defaultdict(set)
    labels: dict[str, str] = {}
    for row in memberships:
        topics_by_document[row["document_key"]].add(row["topic_key"])
        labels[row["topic_key"]] = row["topic_label"]

    counts: Counter[tuple[str, str]] = Counter()
    for topic_keys in topics_by_document.values():
        ordered = sorted(topic_keys)
        for index, source in enumerate(ordered):
            for target in ordered[index + 1 :]:
                counts[(source, target)] += 1

    result = [
        {
            "source": source,
            "target": target,
            "source_label": labels.get(source, source),
            "target_label": labels.get(target, target),
            "document_count": count,
        }
        for (source, target), count in counts.items()
        if count >= min_documents
    ]
    return sorted(result, key=lambda row: (-row["document_count"], row["source"], row["target"]))


def community_rollups(
    repository: KnowledgeRepository,
    *,
    include_drafts: bool = False,
) -> list[dict[str, Any]]:
    """Return stored community descriptions plus live, publication-scoped membership."""
    rows = repository.client.aql(
        """
        /* viz:communities */
        FOR community IN communities
          LET documents = (
            FOR edge IN document_in_community
              FILTER edge._to == community._id
              LET document = DOCUMENT(edge._from)
              FILTER document != null
              SORT document._key ASC
              RETURN KEEP(document, ["_key", "title", "source_key", "published_at", "url", "status"])
          )
          RETURN {
            community: KEEP(community, ["_key", "summary", "top_topics", "size"]),
            documents: documents
          }
        """,
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        community = row.get("community")
        if not isinstance(community, dict):
            continue
        community_key = _key(community)
        if community_key is None:
            continue
        source_documents = [document for document in row.get("documents", []) if isinstance(document, dict)]
        # Stored summaries/top_topics are computed from every member during community rebuild. If
        # even one member is outside the requested publication scope, exporting those stored fields
        # could reveal a draft-only topic. Drop the tainted rollup entirely; its otherwise-visible
        # documents fall into the explicit unclustered zone instead of inheriting hidden metadata.
        if any(not _document_is_visible(document, include_drafts=include_drafts) for document in source_documents):
            continue
        members = {document_key for document in source_documents if (document_key := _key(document)) is not None}
        stored_topics = community.get("top_topics")
        top_topics = [str(topic) for topic in stored_topics if str(topic)] if isinstance(stored_topics, list) else []
        result.append(
            {
                "community_key": community_key,
                "label": " · ".join(top_topics[:2]) if top_topics else community_key,
                "size": len(members),
                "summary": community.get("summary"),
                "top_topics": top_topics,
                "documents": sorted(members),
            }
        )
    return sorted(result, key=lambda row: (-row["size"], row["community_key"]))


def timeline_buckets(
    repository: KnowledgeRepository,
    *,
    top_topics: int = 10,
    include_drafts: bool = False,
) -> dict[str, Any]:
    """Build month-by-source and month-by-top-topic publication buckets."""
    if top_topics < 0:
        raise ValueError("top_topics must not be negative")
    documents = _documents(repository, include_drafts=include_drafts)
    documents_by_key = {row["document_key"]: row for row in documents}
    month_by_document: dict[str, str] = {}
    docs_without_dates = 0
    by_source: Counter[tuple[str, str]] = Counter()
    for document in documents:
        month = _publication_month(document.get("published_at"))
        if month is None:
            docs_without_dates += 1
            continue
        document_key = document["document_key"]
        month_by_document[document_key] = month
        by_source[(month, str(document.get("source_key") or "unknown"))] += 1

    memberships = document_topic_memberships(repository, include_drafts=include_drafts)
    topic_documents: dict[str, set[str]] = defaultdict(set)
    topic_labels: dict[str, str] = {}
    for membership in memberships:
        document_key = membership["document_key"]
        if document_key not in documents_by_key:
            continue
        topic_key = membership["topic_key"]
        topic_documents[topic_key].add(document_key)
        topic_labels[topic_key] = membership["topic_label"]

    ranked_topics = sorted(topic_documents, key=lambda key: (-len(topic_documents[key]), key))[:top_topics]
    selected_topics = set(ranked_topics)
    by_topic: Counter[tuple[str, str]] = Counter()
    for membership in memberships:
        topic_key = membership["topic_key"]
        month = month_by_document.get(membership["document_key"])
        if topic_key in selected_topics and month is not None:
            by_topic[(month, topic_key)] += 1

    dated_months = sorted(set(month_by_document.values()))
    months = _month_range(dated_months[0], dated_months[-1]) if dated_months else []
    return {
        "months": months,
        "by_source": [
            {"month": month, "source_key": source_key, "documents": count}
            for (month, source_key), count in sorted(by_source.items())
        ],
        "topics": [
            {
                "topic_key": topic_key,
                "label": topic_labels.get(topic_key, topic_key),
                "documents": len(topic_documents[topic_key]),
            }
            for topic_key in ranked_topics
        ],
        "by_topic": [
            {"month": month, "topic_key": topic_key, "documents": count} for (month, topic_key), count in sorted(by_topic.items())
        ],
        "docs_without_dates": docs_without_dates,
    }


def ego_graph(
    repository: KnowledgeRepository,
    document_key: str,
    *,
    neighbor_limit: int = 10,
    include_drafts: bool = False,
) -> dict[str, Any]:
    """Build an induced one-hop document graph with entities shared with its centre."""
    if neighbor_limit < 0:
        raise ValueError("neighbor_limit must not be negative")
    documents = _documents(repository, include_drafts=include_drafts)
    documents_by_key = {row["document_key"]: row for row in documents}
    center = documents_by_key.get(document_key)
    if center is None:
        raise ValueError(f"unknown or unpublished document: {document_key}")

    similarity_pairs = _document_similarity_pairs(repository, include_drafts=include_drafts)
    incident = [edge for edge in similarity_pairs if document_key in {edge["source"], edge["target"]}]
    incident.sort(key=lambda edge: (-edge["_weight"], _other_endpoint(edge, document_key)))
    neighbours = [_other_endpoint(edge, document_key) for edge in incident[:neighbor_limit]]
    selected_keys = {document_key, *neighbours}
    induced_edges = [
        _public_similarity_edge(edge)
        for edge in similarity_pairs
        if edge["source"] in selected_keys and edge["target"] in selected_keys
    ]

    entity_memberships = [
        {
            "document_key": row["document_key"],
            "entity_type": "topic",
            "entity_key": row["topic_key"],
            "label": row["topic_label"],
        }
        for row in document_topic_memberships(repository, include_drafts=include_drafts)
        if row["document_key"] in selected_keys
    ]
    for edge_collection, entity_type, label_field in (
        ("document_mentions_author", "author", "display_name"),
        ("document_references_work", "work", "title"),
    ):
        entity_memberships.extend(
            _document_entity_memberships(
                repository,
                edge_collection=edge_collection,
                entity_type=entity_type,
                label_field=label_field,
                include_drafts=include_drafts,
                selected_keys=selected_keys,
            )
        )

    entity_documents: dict[tuple[str, str], set[str]] = defaultdict(set)
    entity_labels: dict[tuple[str, str], str] = {}
    for membership in entity_memberships:
        identity = (membership["entity_type"], membership["entity_key"])
        entity_documents[identity].add(membership["document_key"])
        entity_labels[identity] = membership["label"]

    shared = {
        identity: member_keys
        for identity, member_keys in entity_documents.items()
        if document_key in member_keys and len(member_keys) > 1
    }
    entities = [
        {
            "id": f"{entity_type}:{entity_key}",
            "entity_type": entity_type,
            "entity_key": entity_key,
            "label": entity_labels[(entity_type, entity_key)],
            "document_keys": sorted(member_keys),
        }
        for (entity_type, entity_key), member_keys in sorted(shared.items())
    ]
    entity_edges = [
        {"document_key": member_key, "entity_id": entity["id"]} for entity in entities for member_key in entity["document_keys"]
    ]
    ordered_documents = [document_key, *neighbours]
    return {
        "center": center,
        "documents": [documents_by_key[key] for key in ordered_documents],
        "similarity_edges": induced_edges,
        "entities": entities,
        "entity_edges": entity_edges,
    }


def _document_similarity_pairs(
    repository: KnowledgeRepository,
    *,
    include_drafts: bool,
) -> list[dict[str, Any]]:
    rows = repository.client.aql(
        """
        /* viz:similarity */
        FOR edge IN item_related_to_item
          FILTER edge.method == @method
          LET left_endpoint = DOCUMENT(edge._from)
          LET right_endpoint = DOCUMENT(edge._to)
          FILTER left_endpoint != null AND right_endpoint != null
          LET left_key = HAS(left_endpoint, "document_key") ? left_endpoint.document_key : left_endpoint._key
          LET right_key = HAS(right_endpoint, "document_key") ? right_endpoint.document_key : right_endpoint._key
          FILTER left_key != null AND right_key != null AND left_key != right_key
          LET source = left_key < right_key ? left_key : right_key
          LET target = left_key < right_key ? right_key : left_key
          COLLECT distinct_source = source, distinct_target = target
            AGGREGATE weight = MAX(edge.weight), chunk_pairs = SUM(1)
          LET left = DOCUMENT("documents", distinct_source)
          LET right = DOCUMENT("documents", distinct_target)
          FILTER left != null AND right != null
          FILTER (
            @include_drafts
            AND left.status IN ["published", "draft"]
            AND right.status IN ["published", "draft"]
          ) OR (
            NOT @include_drafts
            AND left.status == "published"
            AND right.status == "published"
          )
          SORT distinct_source ASC, distinct_target ASC
          RETURN {
            source: distinct_source,
            target: distinct_target,
            weight: weight,
            chunk_pairs: chunk_pairs
          }
        """,
        {"method": RELATED_EDGE_METHOD, "include_drafts": include_drafts},
        batch_size=100_000,
    )
    pairs: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if row.get("source") is not None and row.get("target") is not None:
            source = str(row["source"])
            target = str(row["target"])
            try:
                weight = float(row.get("weight"))
                chunk_pairs = int(row.get("chunk_pairs"))
            except (TypeError, ValueError):
                continue
            if source == target or not math.isfinite(weight) or chunk_pairs < 1:
                continue
            source, target = sorted((source, target))
            pairs[(source, target)] = {
                "source": source,
                "target": target,
                "_weight": weight,
                "chunk_pairs": chunk_pairs,
            }
            continue
        left = row.get("left")
        right = row.get("right")
        if not isinstance(left, dict) or not isinstance(right, dict):
            continue
        if not _document_is_visible(left, include_drafts=include_drafts) or not _document_is_visible(
            right,
            include_drafts=include_drafts,
        ):
            continue
        left_key = _key(left)
        right_key = _key(right)
        if left_key is None or right_key is None or left_key == right_key:
            continue
        try:
            weight = float(row.get("weight"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(weight):
            continue
        source, target = sorted((left_key, right_key))
        pair = pairs.setdefault(
            (source, target),
            {"source": source, "target": target, "_weight": weight, "chunk_pairs": 0},
        )
        pair["_weight"] = max(pair["_weight"], weight)
        pair["chunk_pairs"] += 1
    return [pairs[key] for key in sorted(pairs)]


def _documents(repository: KnowledgeRepository, *, include_drafts: bool) -> list[dict[str, Any]]:
    rows = repository.client.aql(
        """
        /* viz:documents */
        FOR document IN documents
          FILTER (@include_drafts AND document.status IN ["published", "draft"])
            OR (NOT @include_drafts AND document.status == "published")
          SORT document._key ASC
          RETURN KEEP(document, ["_key", "title", "source_key", "published_at", "url", "status"])
        """,
        {"include_drafts": include_drafts},
    )
    documents = [
        _public_document(row)
        for row in rows
        if isinstance(row, dict) and _document_is_visible(row, include_drafts=include_drafts) and _key(row) is not None
    ]
    return sorted(documents, key=lambda row: row["document_key"])


def _document_entity_memberships(
    repository: KnowledgeRepository,
    *,
    edge_collection: str,
    entity_type: str,
    label_field: str,
    include_drafts: bool,
    selected_keys: set[str],
) -> list[dict[str, str]]:
    rows = repository.client.aql(
        """
        /* viz:entity_mentions */
        FOR edge IN @@edge_collection
          LET endpoint = DOCUMENT(edge._from)
          FILTER endpoint != null
          LET document_key = HAS(endpoint, "document_key") ? endpoint.document_key : endpoint._key
          COLLECT distinct_document_key = document_key, entity_id = edge._to
          LET entity = DOCUMENT(entity_id)
          LET document = DOCUMENT("documents", distinct_document_key)
          FILTER document != null AND entity != null
          FILTER (@include_drafts AND document.status IN ["published", "draft"])
            OR (NOT @include_drafts AND document.status == "published")
          SORT distinct_document_key ASC, entity._key ASC
          RETURN {
            document: KEEP(document, ["_key", "title", "source_key", "published_at", "url", "status"]),
            entity: KEEP(entity, ["_key", @label_field])
          }
        """,
        {"@edge_collection": edge_collection, "label_field": label_field, "include_drafts": include_drafts},
    )
    memberships: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        document = row.get("document")
        entity = row.get("entity")
        if not isinstance(document, dict) or not isinstance(entity, dict):
            continue
        if not _document_is_visible(document, include_drafts=include_drafts):
            continue
        member_key = _key(document)
        entity_key = _key(entity)
        if member_key is None or member_key not in selected_keys or entity_key is None:
            continue
        memberships.setdefault(
            (member_key, entity_key),
            {
                "document_key": member_key,
                "entity_type": entity_type,
                "entity_key": entity_key,
                "label": str(entity.get(label_field) or entity_key),
            },
        )
    return [memberships[key] for key in sorted(memberships)]


def _document_is_visible(document: dict[str, Any], *, include_drafts: bool) -> bool:
    status = document.get("status")
    return status == "published" or (include_drafts and status == "draft")


def _key(document: dict[str, Any]) -> str | None:
    value = document.get("_key")
    return str(value) if value is not None else None


def _public_document(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_key": str(document["_key"]),
        "title": document.get("title"),
        "source_key": document.get("source_key"),
        "published_at": document.get("published_at"),
        "url": document.get("url"),
        "status": document.get("status"),
    }


def _public_similarity_edge(edge: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": edge["source"],
        "target": edge["target"],
        "weight": round(edge["_weight"], 3),
        "chunk_pairs": edge["chunk_pairs"],
    }


def _other_endpoint(edge: dict[str, Any], document_key: str) -> str:
    return edge["target"] if edge["source"] == document_key else edge["source"]


def _publication_month(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _MONTH.match(value)
    return match.group(0) if match else None


def _month_range(start: str, end: str) -> list[str]:
    year, month = (int(value) for value in start.split("-"))
    end_year, end_month = (int(value) for value in end.split("-"))
    result: list[str] = []
    while (year, month) <= (end_year, end_month):
        result.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return result
