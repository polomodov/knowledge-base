from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from knowledge_base.arango import ArangoError
from knowledge_base.embeddings import cosine_similarity
from knowledge_base.freshness import derived_index_stale_codes

if TYPE_CHECKING:
    from knowledge_base.embeddings import EmbeddingProvider
    from knowledge_base.repository import KnowledgeRepository
    from knowledge_base.research_workflow import ResearchRequest

JsonObject = dict[str, Any]

_MAX_OVERFETCH_FACTOR = 10
_MAX_LEAD_LIMIT = 150
_INDEX_TARGETS = ("embeddings", "related", "communities")
_OPTIONAL_CONTEXT_WARNING = "optional corpus/index freshness context is unavailable"
_SEMANTIC_ANN_FALLBACK_WARNING = "semantic_ann_unavailable_used_exact_rescore_fallback"

_PROVENANCE_OWNERSHIP_ERROR = "provenance ownership mismatch in hydrated research chunk"
_CURRENT_CITATION_QUERY_ERROR = "current citation hydration query failed"
_CURRENT_CITATION_ENVELOPE_ERROR = "current citation hydration returned an invalid result envelope"
_CITATION_ID_RE = re.compile(r"cit-[0-9a-f]{16}")
_CITATION_REF_FIELDS = frozenset(
    {
        "citation_id",
        "source_key",
        "document_key",
        "chunk_key",
        "raw_snapshot_key",
        "import_run_key",
    }
)
_CURRENT_CITATION_FIELDS = frozenset(
    {
        "citation_id",
        "document",
        "chunk",
        "document_edge",
        "raw_edge",
        "raw_snapshot",
        "source_edge",
    }
)
_CURRENT_CITATION_ENTITY_FIELDS: dict[str, frozenset[str]] = {
    "document": frozenset(
        {
            "_id",
            "_key",
            "source_key",
            "canonical_id",
            "title",
            "text",
            "language",
            "published_at",
            "url",
            "status",
        }
    ),
    "chunk": frozenset(
        {
            "_id",
            "_key",
            "document_key",
            "ordinal",
            "text",
            "token_count",
            "char_start",
            "char_end",
        }
    ),
    "document_edge": frozenset({"_id", "_key", "_from", "_to", "ordinal"}),
    "raw_edge": frozenset(
        {
            "_id",
            "_key",
            "_from",
            "_to",
            "document_key",
            "char_start",
            "char_end",
            "import_run_key",
        }
    ),
    "raw_snapshot": frozenset({"_id", "_key", "source_key", "captured_at"}),
    "source_edge": frozenset({"_id", "_key", "_from", "_to", "import_run_key", "provenance"}),
}
_SOURCE_PROVENANCE_FIELDS = frozenset({"raw_snapshot_key", "url", "captured_at"})


class ResearchRetrievalError(RuntimeError):
    """Raised when a V5 retrieval row cannot prove its evidence ownership."""


def lexical_chunk_candidates(
    repository: KnowledgeRepository,
    request: ResearchRequest,
) -> list[JsonObject]:
    """Return visibility-scoped BM25 chunk candidates with exact provenance."""
    rows = repository.client.aql(
        """
        /* research:lexical_chunk_candidates */
        FOR chunk IN kb_text_view
          SEARCH (
            ANALYZER(chunk.text IN TOKENS(@query, "text_en"), "text_en")
            OR ANALYZER(chunk.text IN TOKENS(@query, "text_ru"), "text_ru")
          )
          FILTER IS_SAME_COLLECTION("chunks", chunk)
          LET doc = DOCUMENT("documents", chunk.document_key)
          FILTER doc != null
          FILTER doc.status IN @statuses
          FILTER @source_key == null OR doc.source_key == @source_key
          FILTER @published_from == null OR doc.published_at >= @published_from
          FILTER @published_to_exclusive == null OR doc.published_at < @published_to_exclusive
          LET bm25 = BM25(chunk)
          SORT bm25 DESC, doc._key ASC, chunk.ordinal ASC, chunk._key ASC
          LIMIT @candidate_limit
          LET source_edge = FIRST(
            FOR edge IN document_from_source
              FILTER edge._from == doc._id
              FILTER edge._to == CONCAT("sources/", doc.source_key)
              SORT edge.provenance.captured_at DESC, edge.import_run_key DESC, edge._key ASC
              LIMIT 1
              RETURN edge
          )
          LET raw_edge = FIRST(
            FOR edge IN chunk_derived_from_raw
              FILTER edge._from == chunk._id
              FILTER source_edge != null AND edge.import_run_key == source_edge.import_run_key
              SORT edge._key ASC
              LIMIT 1
              RETURN edge
          )
          LET raw_snapshot = raw_edge == null ? null : DOCUMENT(raw_edge._to)
          RETURN {
            chunk: KEEP(
              chunk, "_id", "_key", "document_key", "ordinal", "text", "token_count",
              "char_start", "char_end", "embedding", "embedding_model"
            ),
            document: KEEP(
              doc, "_id", "_key", "source_key", "canonical_id", "title", "text",
              "language", "published_at", "url", "status"
            ),
            raw_edge: raw_edge == null ? null : KEEP(
              raw_edge, "_id", "_key", "_from", "_to", "document_key",
              "char_start", "char_end", "import_run_key"
            ),
            raw_snapshot: raw_snapshot == null ? null : KEEP(
              raw_snapshot, "_id", "_key", "source_key", "captured_at"
            ),
            source_edge: source_edge == null ? null : {
              _id: source_edge._id,
              _key: source_edge._key,
              _from: source_edge._from,
              _to: source_edge._to,
              import_run_key: source_edge.import_run_key,
              provenance: KEEP(source_edge.provenance, "raw_snapshot_key", "url", "captured_at")
            },
            bm25: bm25
          }
        """,
        {
            **_scope_bind_vars(request),
            "query": request.query,
            "candidate_limit": request.candidate_limit,
        },
    )
    hydrated = _validated_hydrated_rows(rows)
    results: list[JsonObject] = []
    for row in hydrated:
        bm25 = row.get("bm25")
        if isinstance(bm25, bool) or not isinstance(bm25, int | float) or not math.isfinite(bm25):
            raise ResearchRetrievalError("lexical candidate has an invalid BM25 score")
        results.append({**row, "score_components": {"lexical": float(bm25), "vector": None}})
    return results


def semantic_chunk_candidates(
    repository: KnowledgeRepository,
    request: ResearchRequest,
    *,
    provider: EmbeddingProvider,
    overfetch_factor: int = 4,
) -> tuple[list[JsonObject], tuple[str, ...]]:
    """Discover with bounded ANN, then scope, hydrate and exact-rescore chunks.

    Returns ``(rows, warnings)``. When pre-filtered ANN is unavailable, falls back to a
    scoped exact cosine rescore and reports ``semantic_ann_unavailable_used_exact_rescore_fallback``.
    """
    if isinstance(overfetch_factor, bool) or not 1 <= overfetch_factor <= _MAX_OVERFETCH_FACTOR:
        raise ValueError(f"overfetch_factor must be between 1 and {_MAX_OVERFETCH_FACTOR}")

    query_vector = _numeric_vector(provider.embed(request.query), expected_dimension=provider.dimension)
    candidate_limit = request.candidate_limit
    if candidate_limit is None:
        raise ResearchRetrievalError("research request is missing its effective candidate limit")
    overfetch_limit = candidate_limit * overfetch_factor
    ann_rows, used_fallback = _semantic_discovery(
        repository,
        request,
        query_vector=query_vector,
        embedding_model=provider.model,
        overfetch_limit=overfetch_limit,
    )
    warnings = (_SEMANTIC_ANN_FALLBACK_WARNING,) if used_fallback else ()
    chunk_keys = _candidate_chunk_keys(ann_rows)
    hydrated = hydrate_chunk_candidates(repository, chunk_keys, request)
    threshold = float(repository.client.settings.retrieval_min_similarity)

    scored: list[JsonObject] = []
    for row in hydrated:
        chunk = _mapping_field(row, "chunk")
        stored = chunk.get("embedding")
        if not isinstance(stored, list) or len(stored) != provider.dimension:
            continue
        try:
            embedding = _numeric_vector(stored, expected_dimension=provider.dimension)
        except ResearchRetrievalError:
            continue
        score = cosine_similarity(query_vector, embedding)
        if score >= threshold:
            scored.append({**row, "score_components": {"lexical": None, "vector": score}})

    scored.sort(key=_semantic_sort_key)
    return scored[:candidate_limit], warnings


def hydrate_chunk_candidates(
    repository: KnowledgeRepository,
    chunk_keys: Sequence[str],
    request: ResearchRequest,
) -> list[JsonObject]:
    """Hydrate exact persisted chunks and their allowlisted provenance chain."""
    keys = _unique_keys(chunk_keys, kind="chunk")
    if not keys:
        return []
    rows = repository.client.aql(
        """
        /* research:hydrate_chunk_candidates */
        FOR chunk_key IN @chunk_keys
          LET chunk = DOCUMENT("chunks", chunk_key)
          FILTER chunk != null
          LET doc = DOCUMENT("documents", chunk.document_key)
          FILTER doc != null
          FILTER doc.status IN @statuses
          FILTER @source_key == null OR doc.source_key == @source_key
          FILTER @published_from == null OR doc.published_at >= @published_from
          FILTER @published_to_exclusive == null OR doc.published_at < @published_to_exclusive
          LET source_edge = FIRST(
            FOR edge IN document_from_source
              FILTER edge._from == doc._id
              FILTER edge._to == CONCAT("sources/", doc.source_key)
              SORT edge.provenance.captured_at DESC, edge.import_run_key DESC, edge._key ASC
              LIMIT 1
              RETURN edge
          )
          LET raw_edge = FIRST(
            FOR edge IN chunk_derived_from_raw
              FILTER edge._from == chunk._id
              FILTER source_edge != null AND edge.import_run_key == source_edge.import_run_key
              SORT edge._key ASC
              LIMIT 1
              RETURN edge
          )
          LET raw_snapshot = raw_edge == null ? null : DOCUMENT(raw_edge._to)
          RETURN {
            chunk: KEEP(
              chunk, "_id", "_key", "document_key", "ordinal", "text", "token_count",
              "char_start", "char_end", "embedding", "embedding_model"
            ),
            document: KEEP(
              doc, "_id", "_key", "source_key", "canonical_id", "title", "text",
              "language", "published_at", "url", "status"
            ),
            raw_edge: raw_edge == null ? null : KEEP(
              raw_edge, "_id", "_key", "_from", "_to", "document_key",
              "char_start", "char_end", "import_run_key"
            ),
            raw_snapshot: raw_snapshot == null ? null : KEEP(
              raw_snapshot, "_id", "_key", "source_key", "captured_at"
            ),
            source_edge: source_edge == null ? null : {
              _id: source_edge._id,
              _key: source_edge._key,
              _from: source_edge._from,
              _to: source_edge._to,
              import_run_key: source_edge.import_run_key,
              provenance: KEEP(source_edge.provenance, "raw_snapshot_key", "url", "captured_at")
            }
          }
        """,
        {**_scope_bind_vars(request), "chunk_keys": keys},
    )
    return _validated_hydrated_rows(rows)


def hydrate_current_citations(
    repository: KnowledgeRepository,
    citation_refs: Sequence[Mapping[str, Any]],
) -> list[JsonObject]:
    """Hydrate a bounded citation set without applying its former visibility scope."""
    refs = _validated_citation_refs(citation_refs)
    try:
        rows = repository.client.aql(
            """
            /* research:hydrate_current_citations */
            FOR ref IN @citation_refs
              LET document = DOCUMENT("documents", ref.document_key)
              LET chunk = DOCUMENT("chunks", ref.chunk_key)
              LET document_edge = FIRST(
                FOR edge IN chunk_of_document
                  FILTER edge._from == CONCAT("chunks/", ref.chunk_key)
                  FILTER edge._to == CONCAT("documents/", ref.document_key)
                  SORT edge._key ASC
                  LIMIT 1
                  RETURN edge
              )
              LET source_edge = FIRST(
                FOR edge IN document_from_source
                  FILTER edge._from == CONCAT("documents/", ref.document_key)
                  FILTER edge._to == CONCAT("sources/", ref.source_key)
                  FILTER edge.import_run_key == ref.import_run_key
                  FILTER edge.provenance.raw_snapshot_key == ref.raw_snapshot_key
                  SORT edge._key ASC
                  LIMIT 1
                  RETURN edge
              )
              LET raw_edge = FIRST(
                FOR edge IN chunk_derived_from_raw
                  FILTER ref.raw_snapshot_key != null
                  FILTER edge._from == CONCAT("chunks/", ref.chunk_key)
                  FILTER edge._to == CONCAT("raw_snapshots/", ref.raw_snapshot_key)
                  FILTER edge.document_key == ref.document_key
                  FILTER edge.import_run_key == ref.import_run_key
                  SORT edge._key ASC
                  LIMIT 1
                  RETURN edge
              )
              LET raw_snapshot = raw_edge == null ? null : DOCUMENT(
                "raw_snapshots", ref.raw_snapshot_key
              )
              RETURN {
                citation_id: ref.citation_id,
                document: document == null ? null : KEEP(
                  document, "_id", "_key", "source_key", "canonical_id", "title", "text",
                  "language", "published_at", "url", "status"
                ),
                chunk: chunk == null ? null : KEEP(
                  chunk, "_id", "_key", "document_key", "ordinal", "text", "token_count",
                  "char_start", "char_end"
                ),
                document_edge: document_edge == null ? null : KEEP(
                  document_edge, "_id", "_key", "_from", "_to", "ordinal"
                ),
                raw_edge: raw_edge == null ? null : KEEP(
                  raw_edge, "_id", "_key", "_from", "_to", "document_key",
                  "char_start", "char_end", "import_run_key"
                ),
                raw_snapshot: raw_snapshot == null ? null : KEEP(
                  raw_snapshot, "_id", "_key", "source_key", "captured_at"
                ),
                source_edge: source_edge == null ? null : {
                  _id: source_edge._id,
                  _key: source_edge._key,
                  _from: source_edge._from,
                  _to: source_edge._to,
                  import_run_key: source_edge.import_run_key,
                  provenance: KEEP(source_edge.provenance, "raw_snapshot_key", "url", "captured_at")
                }
              }
            """,
            {"citation_refs": refs},
        )
    except ArangoError:
        raise ResearchRetrievalError(_CURRENT_CITATION_QUERY_ERROR) from None
    return _validated_current_citation_rows(rows, refs)


def topic_leads(
    repository: KnowledgeRepository,
    document_keys: Sequence[str],
    request: ResearchRequest,
    *,
    limit: int,
) -> list[JsonObject]:
    """Group only visible anchor documents by their visible topic edges."""
    keys = _unique_keys(document_keys, kind="document")
    if not keys:
        return []
    rows = repository.client.aql(
        """
        /* research:topic_leads */
        FOR document_key IN @document_keys
          LET doc = DOCUMENT("documents", document_key)
          FILTER doc != null
          FILTER doc.status IN @statuses
          FILTER @source_key == null OR doc.source_key == @source_key
          FILTER @published_from == null OR doc.published_at >= @published_from
          FILTER @published_to_exclusive == null OR doc.published_at < @published_to_exclusive
          LET chunk_ids = (
            FOR chunk IN chunks
              FILTER chunk.document_key == doc._key
              RETURN chunk._id
          )
          LET mention_from_ids = APPEND([doc._id], chunk_ids)
          FOR edge IN document_mentions_topic
            FILTER edge._from IN mention_from_ids
            LET topic = DOCUMENT(edge._to)
            FILTER topic != null AND IS_SAME_COLLECTION("topics", topic)
            COLLECT
              topic_key = topic._key,
              label = topic.label,
              language = topic.language,
              description = topic.description
              INTO mentions = {
                document_key: doc._key,
                document_status: doc.status
              }
            LET grouped_document_keys = UNIQUE(mentions[*].document_key)
            LET document_statuses = UNIQUE(mentions[*].document_status)
            SORT LENGTH(grouped_document_keys) DESC, topic_key ASC
            LIMIT @limit
            RETURN {
              topic_key: topic_key,
              label: label,
              language: language,
              description: description,
              document_keys: grouped_document_keys,
              document_statuses: document_statuses
            }
        """,
        {
            **_scope_bind_vars(request),
            "document_keys": keys,
            "limit": _lead_limit(limit),
        },
    )
    return [row for row in rows if _all_statuses_allowed(row.get("document_statuses"), request.document_statuses)]


def related_leads(
    repository: KnowledgeRepository,
    chunk_keys: Sequence[str],
    request: ResearchRequest,
    *,
    limit: int,
) -> list[JsonObject]:
    """Return scoped chunks connected to visible anchor chunks by related edges."""
    keys = _unique_keys(chunk_keys, kind="chunk")
    if not keys:
        return []
    rows = repository.client.aql(
        """
        /* research:related_leads */
        FOR anchor_key IN @chunk_keys
          LET anchor_chunk = DOCUMENT("chunks", anchor_key)
          FILTER anchor_chunk != null
          LET anchor_doc = DOCUMENT("documents", anchor_chunk.document_key)
          FILTER anchor_doc != null
          FILTER anchor_doc.status IN @statuses
          FILTER @source_key == null OR anchor_doc.source_key == @source_key
          FILTER @published_from == null OR anchor_doc.published_at >= @published_from
          FILTER @published_to_exclusive == null OR anchor_doc.published_at < @published_to_exclusive
          FOR edge IN item_related_to_item
            FILTER edge._from == anchor_chunk._id OR edge._to == anchor_chunk._id
            LET related_id = edge._from == anchor_chunk._id ? edge._to : edge._from
            LET related_chunk = DOCUMENT(related_id)
            FILTER related_chunk != null AND IS_SAME_COLLECTION("chunks", related_chunk)
            FILTER related_chunk._key NOT IN @chunk_keys
            LET doc = DOCUMENT("documents", related_chunk.document_key)
            FILTER doc != null
            FILTER doc.status IN @statuses
            FILTER @source_key == null OR doc.source_key == @source_key
            FILTER @published_from == null OR doc.published_at >= @published_from
            FILTER @published_to_exclusive == null OR doc.published_at < @published_to_exclusive
            COLLECT
              document_key = doc._key,
              chunk_key = related_chunk._key
              AGGREGATE weight = MAX(edge.weight)
            LET grouped_doc = DOCUMENT("documents", document_key)
            SORT weight DESC, document_key ASC, chunk_key ASC
            LIMIT @limit
            RETURN {
              document_key: document_key,
              chunk_key: chunk_key,
              title: grouped_doc.title,
              document_status: grouped_doc.status,
              source_key: grouped_doc.source_key,
              published_at: grouped_doc.published_at,
              weight: weight
            }
        """,
        {
            **_scope_bind_vars(request),
            "chunk_keys": keys,
            "limit": _lead_limit(limit),
        },
    )
    return [row for row in rows if row.get("document_status") in request.document_statuses]


def clean_community_leads(
    repository: KnowledgeRepository,
    document_keys: Sequence[str],
    request: ResearchRequest,
    *,
    limit: int,
) -> list[JsonObject]:
    """Return a stored community only when every current member is in scope."""
    keys = _unique_keys(document_keys, kind="document")
    if not keys:
        return []
    rows = repository.client.aql(
        """
        /* research:clean_community_leads */
        FOR document_key IN @document_keys
          LET doc = DOCUMENT("documents", document_key)
          FILTER doc != null
          FILTER doc.status IN @statuses
          FILTER @source_key == null OR doc.source_key == @source_key
          FILTER @published_from == null OR doc.published_at >= @published_from
          FILTER @published_to_exclusive == null OR doc.published_at < @published_to_exclusive
          FOR membership IN document_in_community
            FILTER membership._from == doc._id
            LET community = DOCUMENT(membership._to)
            FILTER community != null
            LET disallowed_members = (
              FOR member_edge IN document_in_community
                FILTER member_edge._to == community._id
                LET member = DOCUMENT(member_edge._from)
                FILTER member == null OR NOT (
                  member.status IN @statuses
                  AND (@source_key == null OR member.source_key == @source_key)
                  AND (@published_from == null OR member.published_at >= @published_from)
                  AND (@published_to_exclusive == null OR member.published_at < @published_to_exclusive)
                )
                RETURN member_edge._from
            )
            FILTER LENGTH(disallowed_members) == 0
            COLLECT community_key = community._key
            LET clean = DOCUMENT("communities", community_key)
            SORT clean.size DESC, community_key ASC
            LIMIT @limit
            RETURN {
              community_key: community_key,
              size: clean.size,
              method: clean.method,
              top_topics: clean.top_topics,
              summary: clean.summary,
              is_clean: true
            }
        """,
        {
            **_scope_bind_vars(request),
            "document_keys": keys,
            "limit": _lead_limit(limit),
        },
    )
    return [row for row in rows if _community_is_clean(row)]


def load_corpus_context(
    repository: KnowledgeRepository,
    request: ResearchRequest,
    *,
    provider: EmbeddingProvider,
    built_at: str,
    git_revision: str | None,
) -> JsonObject:
    """Load allowlisted freshness metadata; an unavailable optional query degrades."""
    settings = repository.client.settings
    context: JsonObject = {
        "database": settings.arango_database,
        "built_at": built_at,
        "embedding_model": provider.model,
        "embedding_dimension": provider.dimension,
        "retrieval_min_similarity": settings.retrieval_min_similarity,
        "latest_import_run_key": None,
        "latest_index_runs": {},
        "git_revision": git_revision,
        "warnings": [],
    }
    try:
        rows = repository.client.aql(
            """
            /* research:corpus_context */
            LET latest_import = FIRST(
              FOR run IN import_runs
                FILTER run.status == "ok"
                FILTER @source_key == null OR run.source_key == @source_key
                SORT run.finished_at DESC, run.started_at DESC, run._key DESC
                LIMIT 1
                RETURN run._key
            )
            LET index_rows = (
              FOR target IN @index_targets
                LET latest = FIRST(
                  FOR run IN index_runs
                    FILTER run.status == "ok" AND run.target == target
                    SORT run.finished_at DESC, run.started_at DESC, run._key DESC
                    LIMIT 1
                    RETURN {
                      run_key: run._key,
                      started_at: run.started_at,
                      finished_at: run.finished_at
                    }
                )
                FILTER latest != null
                RETURN { target: target, run: latest }
            )
            RETURN {
              latest_import_run_key: latest_import,
              latest_index_runs: ZIP(index_rows[*].target, index_rows[*].run)
            }
            """,
            {"source_key": request.source_key, "index_targets": list(_INDEX_TARGETS)},
        )
    except ArangoError:
        context["warnings"] = [_OPTIONAL_CONTEXT_WARNING]
        return context

    if not rows or not isinstance(rows[0], Mapping):
        context["warnings"] = [_OPTIONAL_CONTEXT_WARNING]
        return context
    context["latest_import_run_key"] = _optional_string(rows[0].get("latest_import_run_key"))
    context["latest_index_runs"] = _sanitize_index_runs(rows[0].get("latest_index_runs"))
    context["warnings"] = list(derived_index_stale_codes(context["latest_index_runs"]))
    return context


def _semantic_discovery(
    repository: KnowledgeRepository,
    request: ResearchRequest,
    *,
    query_vector: list[float],
    embedding_model: str,
    overfetch_limit: int,
) -> tuple[list[Any], bool]:
    bind_vars = {
        **_scope_bind_vars(request),
        "query_embedding": query_vector,
        "embedding_model": embedding_model,
        "overfetch_limit": overfetch_limit,
    }
    try:
        rows = repository.client.aql(
            """
            /* research:semantic_chunk_candidates */
            FOR chunk IN chunks
              FILTER chunk.embedding_model == @embedding_model
              LET doc = DOCUMENT("documents", chunk.document_key)
              FILTER doc != null
              FILTER doc.status IN @statuses
              FILTER @source_key == null OR doc.source_key == @source_key
              FILTER @published_from == null OR doc.published_at >= @published_from
              FILTER @published_to_exclusive == null OR doc.published_at < @published_to_exclusive
              LET approximate_score = APPROX_NEAR_COSINE(chunk.embedding, @query_embedding)
              SORT approximate_score DESC
              LIMIT @overfetch_limit
              RETURN {
                chunk_key: chunk._key,
                approximate_score: approximate_score
              }
            """,
            bind_vars,
        )
        return rows, False
    except ArangoError:
        # ArangoDB before 3.12.6 and some vector-index plans reject pre-filtered
        # APPROX_NEAR_COSINE queries. Never fall back to an unscoped ANN result:
        # exact-rescore a scoped batch in Python and let the caller mark degraded.
        return _semantic_fallback_exact_window(
            repository,
            request,
            query_vector=query_vector,
            embedding_model=embedding_model,
            overfetch_limit=overfetch_limit,
        ), True


def _semantic_fallback_exact_window(
    repository: KnowledgeRepository,
    request: ResearchRequest,
    *,
    query_vector: list[float],
    embedding_model: str,
    overfetch_limit: int,
    batch_size: int = 500,
) -> list[Any]:
    """Load scoped embeddings in batches, exact-cosine rank, keep top overfetch_limit."""
    scored: list[tuple[float, str]] = []
    offset = 0
    while True:
        batch = repository.client.aql(
            """
            /* research:scoped_semantic_fallback */
            FOR chunk IN chunks
              FILTER chunk.embedding_model == @embedding_model
              FILTER HAS(chunk, "embedding")
              LET doc = DOCUMENT("documents", chunk.document_key)
              FILTER doc != null
              FILTER doc.status IN @statuses
              FILTER @source_key == null OR doc.source_key == @source_key
              FILTER @published_from == null OR doc.published_at >= @published_from
              FILTER @published_to_exclusive == null OR doc.published_at < @published_to_exclusive
              SORT chunk._key ASC
              LIMIT @offset, @batch_size
              RETURN {
                chunk_key: chunk._key,
                embedding: chunk.embedding
              }
            """,
            {
                **_scope_bind_vars(request),
                "embedding_model": embedding_model,
                "offset": offset,
                "batch_size": batch_size,
            },
        )
        if not batch:
            break
        for row in batch:
            if not isinstance(row, Mapping):
                continue
            key = row.get("chunk_key")
            embedding = row.get("embedding")
            if not isinstance(key, str) or not isinstance(embedding, list):
                continue
            try:
                vector = _numeric_vector(embedding, expected_dimension=len(query_vector))
            except ResearchRetrievalError:
                continue
            scored.append((cosine_similarity(query_vector, vector), key))
        if len(batch) < batch_size:
            break
        offset += batch_size
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [{"chunk_key": key, "approximate_score": score} for score, key in scored[:overfetch_limit]]


def _scope_bind_vars(request: ResearchRequest) -> JsonObject:
    return {
        "statuses": list(request.document_statuses),
        "source_key": request.source_key,
        "published_from": request.published_from_utc,
        "published_to_exclusive": request.published_to_exclusive_utc,
    }


def _candidate_chunk_keys(rows: Sequence[Any]) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        key = row.get("chunk_key")
        if isinstance(key, str) and key and "/" not in key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def _validated_citation_refs(citation_refs: Sequence[Mapping[str, Any]]) -> list[JsonObject]:
    if isinstance(citation_refs, str | bytes) or not 1 <= len(citation_refs) <= 100:
        raise ValueError("citation_refs must contain 1..100 items")

    refs: list[JsonObject] = []
    citation_ids: set[str] = set()
    for ref in citation_refs:
        if not isinstance(ref, Mapping) or set(ref) != _CITATION_REF_FIELDS:
            raise ValueError("citation refs must use the exact allowlisted fields")
        citation_id = ref["citation_id"]
        if not isinstance(citation_id, str) or _CITATION_ID_RE.fullmatch(citation_id) is None:
            raise ValueError("citation refs contain an invalid citation_id")
        if citation_id in citation_ids:
            raise ValueError("citation refs must contain unique citation IDs")
        citation_ids.add(citation_id)

        normalized: JsonObject = {"citation_id": citation_id}
        for field in ("source_key", "document_key", "chunk_key"):
            normalized[field] = _citation_ref_key(ref[field], field=field, optional=False)
        for field in ("raw_snapshot_key", "import_run_key"):
            normalized[field] = _citation_ref_key(ref[field], field=field, optional=True)
        refs.append(normalized)
    return refs


def _citation_ref_key(value: Any, *, field: str, optional: bool) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value or len(value) > 256 or "/" in value:
        suffix = " or null" if optional else ""
        raise ValueError(f"citation ref {field} must be a collection-local key{suffix}")
    return value


def _validated_current_citation_rows(rows: Any, refs: Sequence[Mapping[str, Any]]) -> list[JsonObject]:
    if not isinstance(rows, Sequence) or isinstance(rows, str | bytes) or len(rows) != len(refs):
        raise ResearchRetrievalError(_CURRENT_CITATION_ENVELOPE_ERROR)

    expected_ids = [str(ref["citation_id"]) for ref in refs]
    expected_id_set = set(expected_ids)
    by_id: dict[str, JsonObject] = {}
    for row in rows:
        sanitized = _sanitize_current_citation_row(row)
        citation_id = str(sanitized["citation_id"])
        if citation_id not in expected_id_set or citation_id in by_id:
            raise ResearchRetrievalError(_CURRENT_CITATION_ENVELOPE_ERROR)
        by_id[citation_id] = sanitized
    if set(by_id) != expected_id_set:
        raise ResearchRetrievalError(_CURRENT_CITATION_ENVELOPE_ERROR)
    return [by_id[citation_id] for citation_id in expected_ids]


def _sanitize_current_citation_row(row: Any) -> JsonObject:
    if not isinstance(row, Mapping) or set(row) != _CURRENT_CITATION_FIELDS:
        raise ResearchRetrievalError(_CURRENT_CITATION_ENVELOPE_ERROR)
    citation_id = row.get("citation_id")
    if not isinstance(citation_id, str) or _CITATION_ID_RE.fullmatch(citation_id) is None:
        raise ResearchRetrievalError(_CURRENT_CITATION_ENVELOPE_ERROR)

    sanitized: JsonObject = {"citation_id": citation_id}
    for field, allowed_fields in _CURRENT_CITATION_ENTITY_FIELDS.items():
        value = row.get(field)
        if value is None:
            sanitized[field] = None
            continue
        if not isinstance(value, Mapping) or not set(value).issubset(allowed_fields):
            raise ResearchRetrievalError(_CURRENT_CITATION_ENVELOPE_ERROR)
        entity = dict(value)
        if field == "source_edge" and "provenance" in entity:
            provenance = entity["provenance"]
            if not isinstance(provenance, Mapping) or not set(provenance).issubset(_SOURCE_PROVENANCE_FIELDS):
                raise ResearchRetrievalError(_CURRENT_CITATION_ENVELOPE_ERROR)
            entity["provenance"] = dict(provenance)
        sanitized[field] = entity
    return sanitized


def _unique_keys(values: Sequence[str], *, kind: str) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value or "/" in value:
            raise ValueError(f"{kind} keys must be non-empty collection-local keys")
        if value not in seen:
            seen.add(value)
            keys.append(value)
    return keys


def _lead_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_LEAD_LIMIT:
        raise ValueError(f"lead limit must be between 1 and {_MAX_LEAD_LIMIT}")
    return limit


def _numeric_vector(values: Sequence[Any], *, expected_dimension: int) -> list[float]:
    if len(values) != expected_dimension:
        raise ResearchRetrievalError(f"embedding dimension mismatch: expected {expected_dimension}, got {len(values)}")
    result: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            raise ResearchRetrievalError("embedding contains a non-finite or non-numeric value")
        result.append(float(value))
    return result


def _validated_hydrated_rows(rows: Sequence[Any]) -> list[JsonObject]:
    validated: list[JsonObject] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ResearchRetrievalError("retrieval hydration returned a non-object row")
        _validate_hydrated_row(row)
        validated.append(row)
    return validated


def _validate_hydrated_row(row: Mapping[str, Any]) -> None:
    chunk = _mapping_field(row, "chunk")
    document = _mapping_field(row, "document")
    raw_edge = _mapping_field(row, "raw_edge")
    raw_snapshot = _mapping_field(row, "raw_snapshot")
    source_edge = _mapping_field(row, "source_edge")

    chunk_key = _required_string(chunk, "_key")
    document_key = _required_string(document, "_key")
    source_key = _required_string(document, "source_key")
    raw_key = _required_string(raw_snapshot, "_key")
    expected_chunk_id = f"chunks/{chunk_key}"
    expected_document_id = f"documents/{document_key}"
    expected_raw_id = f"raw_snapshots/{raw_key}"
    expected_source_id = f"sources/{source_key}"

    checks = (
        chunk.get("document_key") == document_key,
        raw_edge.get("_from") == expected_chunk_id,
        raw_edge.get("_to") == expected_raw_id,
        raw_edge.get("document_key") == document_key,
        raw_snapshot.get("source_key") == source_key,
        source_edge.get("_from") == expected_document_id,
        source_edge.get("_to") == expected_source_id,
    )
    if not all(checks):
        raise ResearchRetrievalError(_PROVENANCE_OWNERSHIP_ERROR)

    for entity, expected_id in ((chunk, expected_chunk_id), (document, expected_document_id)):
        if entity.get("_id", expected_id) != expected_id:
            raise ResearchRetrievalError(_PROVENANCE_OWNERSHIP_ERROR)
    if raw_snapshot.get("_id", expected_raw_id) != expected_raw_id:
        raise ResearchRetrievalError(_PROVENANCE_OWNERSHIP_ERROR)

    document_text = document.get("text")
    chunk_text = chunk.get("text")
    char_start = chunk.get("char_start")
    char_end = chunk.get("char_end")
    normalized_document_text = " ".join(document_text.split()) if isinstance(document_text, str) else None
    if (
        normalized_document_text is None
        or not isinstance(chunk_text, str)
        or not isinstance(char_start, int)
        or isinstance(char_start, bool)
        or not isinstance(char_end, int)
        or isinstance(char_end, bool)
        or not 0 <= char_start < char_end <= len(normalized_document_text)
        or normalized_document_text[char_start:char_end] != chunk_text
    ):
        raise ResearchRetrievalError("provenance offsets do not resolve to the exact hydrated chunk text")
    if raw_edge.get("char_start") != char_start or raw_edge.get("char_end") != char_end:
        raise ResearchRetrievalError("provenance offsets do not match the hydrated chunk ownership")

    provenance = source_edge.get("provenance")
    if isinstance(provenance, Mapping) and provenance.get("raw_snapshot_key", raw_key) != raw_key:
        raise ResearchRetrievalError(_PROVENANCE_OWNERSHIP_ERROR)
    raw_import = raw_edge.get("import_run_key")
    source_import = source_edge.get("import_run_key")
    if raw_import is not None and source_import is not None and raw_import != source_import:
        raise ResearchRetrievalError("provenance import-run ownership mismatch in hydrated research chunk")


def _mapping_field(row: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    value = row.get(field)
    if not isinstance(value, Mapping):
        raise ResearchRetrievalError(f"hydrated research chunk is missing {field} provenance")
    return value


def _required_string(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value:
        raise ResearchRetrievalError(f"hydrated research provenance is missing {field}")
    return value


def _semantic_sort_key(row: Mapping[str, Any]) -> tuple[float, str, int, str]:
    chunk = _mapping_field(row, "chunk")
    document = _mapping_field(row, "document")
    components = _mapping_field(row, "score_components")
    vector = components.get("vector")
    score = float(vector) if isinstance(vector, int | float) and not isinstance(vector, bool) else -math.inf
    ordinal = chunk.get("ordinal")
    return (
        -score,
        str(document.get("_key", "")),
        int(ordinal) if isinstance(ordinal, int) and not isinstance(ordinal, bool) else 0,
        str(chunk.get("_key", "")),
    )


def _all_statuses_allowed(value: Any, allowed: Sequence[str]) -> bool:
    return isinstance(value, list) and bool(value) and all(status in allowed for status in value)


def _community_is_clean(row: Any) -> bool:
    if not isinstance(row, dict) or row.get("is_clean") is not True:
        return False
    disallowed = row.get("disallowed_members", [])
    return isinstance(disallowed, list) and not disallowed


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _sanitize_index_runs(value: Any) -> JsonObject:
    if not isinstance(value, Mapping):
        return {}
    result: JsonObject = {}
    for target in _INDEX_TARGETS:
        run = value.get(target)
        if not isinstance(run, Mapping):
            continue
        run_key = _optional_string(run.get("run_key"))
        if run_key is None:
            continue
        result[target] = {
            "run_key": run_key,
            "started_at": _optional_string(run.get("started_at")),
            "finished_at": _optional_string(run.get("finished_at")),
        }
    return result
