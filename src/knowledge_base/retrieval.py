from __future__ import annotations

from typing import Any

from knowledge_base.arango import ArangoError
from knowledge_base.constants import VECTOR_DIMENSION
from knowledge_base.embeddings import EmbeddingProvider, HashEmbeddingProvider, cosine_similarity
from knowledge_base.repository import KnowledgeRepository

# GR-1: hybrid folds a bounded graph signal into ranking. Seeds are the strongest fused
# candidates; a document that shares topics/authors/works with them is boosted up to the cap,
# so the graph reinforces the lexical/semantic score without ever dominating it.
_HYBRID_SEED_COUNT = 5
_GRAPH_BOOST_CAP = 0.5


def text_search(
    repository: KnowledgeRepository,
    query: str,
    *,
    limit: int = 10,
    source_key: str | None = None,
) -> dict[str, Any]:
    rows = repository.client.aql(
        """
        FOR item IN kb_text_view
          SEARCH ANALYZER(item.text IN TOKENS(@query, "text_en") OR item.title IN TOKENS(@query, "text_en"), "text_en")
          FILTER IS_SAME_COLLECTION("documents", item) OR IS_SAME_COLLECTION("chunks", item)
          LET is_chunk = HAS(item, "document_key")
          LET doc = is_chunk ? DOCUMENT("documents", item.document_key) : item
          FILTER doc != null
          FILTER @source_key == null OR doc.source_key == @source_key
          LET score = BM25(item)
          COLLECT document_key = doc._key INTO group = { item: item, doc: doc, is_chunk: is_chunk, score: score }
          LET best = FIRST(FOR candidate IN group SORT candidate.score DESC LIMIT 1 RETURN candidate)
          SORT best.score DESC
          LIMIT @limit
          LET item = best.item
          LET doc = best.doc
          LET is_chunk = best.is_chunk
          LET score = best.score
          LET anchor_chunk = is_chunk ? item : FIRST(
            FOR chunk_doc IN chunks
              FILTER chunk_doc.document_key == doc._key
              SORT chunk_doc.ordinal ASC
              LIMIT 1
              RETURN chunk_doc
          )
          LET raw_edge = anchor_chunk ? FIRST(FOR e IN chunk_derived_from_raw FILTER e._from == anchor_chunk._id RETURN e) : null
          LET raw = raw_edge ? DOCUMENT(raw_edge._to) : null
          LET source_edge = FIRST(FOR e IN document_from_source FILTER e._from == doc._id RETURN e)
          RETURN {
            id: item._id,
            document_key: doc._key,
            chunk_key: is_chunk ? item._key : null,
            title: doc.title,
            snippet: SUBSTRING(item.text, 0, 240),
            score: score,
            score_components: { bm25: score, vector: null, graph_boost: null },
            provenance: {
              source_key: doc.source_key,
              raw_snapshot_key: raw ? raw._key : (
                source_edge != null AND HAS(source_edge, "provenance") ? source_edge.provenance.raw_snapshot_key : null
              ),
              import_run_key: raw_edge ? raw_edge.import_run_key : (
                source_edge ? source_edge.import_run_key : null
              ),
              medium_post: (
                source_edge != null AND HAS(source_edge, "provenance") AND HAS(source_edge.provenance, "medium_post")
                  ? source_edge.provenance.medium_post
                  : null
              ),
              url: doc.url,
              captured_at: raw ? raw.captured_at : null
            }
          }
        """,
        {"query": query, "limit": limit, "source_key": source_key},
    )
    return {"query": query, "mode": "text", "status": "ok", "results": rows}


def semantic_search(
    repository: KnowledgeRepository,
    query: str,
    *,
    limit: int = 10,
    dimension: int = VECTOR_DIMENSION,
    source_key: str | None = None,
    provider: EmbeddingProvider | None = None,
) -> dict[str, Any]:
    # The query is embedded by the same provider that produced the stored chunk vectors, so both
    # live in one space (GR-2). Without an explicit provider we default to the hash embedder at
    # the requested dimension, preserving the original offline behaviour.
    embedder = provider if provider is not None else HashEmbeddingProvider(dimension=dimension)
    query_vector = embedder.embed(query)
    effective_dimension = embedder.dimension
    model = embedder.model
    ranked = _vector_ranked(repository, query_vector, limit=limit, source_key=source_key, model=model)
    if ranked is None:
        # Fallback: full-scan cosine in Python. Used when the ANN index is unavailable,
        # the embedding dimension differs from the index, or a source filter is set (the
        # vector index cannot be combined with a filter).
        chunks = _semantic_candidate_chunks(repository, dimension=effective_dimension, source_key=source_key, model=model)
        if not chunks:
            if source_key is not None:
                return {"query": query, "mode": "semantic", "status": "ok", "results": []}
            return {
                "query": query,
                "mode": "semantic",
                "status": "degraded",
                "degraded_components": ["vector"],
                "results": [],
            }
        scored = [
            {
                "id": chunk["_id"],
                "key": chunk["_key"],
                "document_key": chunk["document_key"],
                "text": chunk["text"],
                "score": _cosine(query_vector, chunk["embedding"]),
            }
            for chunk in chunks
        ]
        scored.sort(key=lambda item: item["score"], reverse=True)
        ranked = _dedup_best_by_document(scored)

    return {"query": query, "mode": "semantic", "status": "ok", "results": _hydrate_semantic(repository, ranked[:limit])}


def _semantic_candidate_chunks(
    repository: KnowledgeRepository,
    *,
    dimension: int,
    source_key: str | None,
    model: str,
    batch_size: int = 500,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    offset = 0
    while True:
        batch = repository.client.aql(
            """
            FOR chunk IN chunks
              FILTER HAS(chunk, "embedding") AND LENGTH(chunk.embedding) == @dimension AND chunk.embedding_model == @model
              LET doc = DOCUMENT("documents", chunk.document_key)
              FILTER doc != null
              FILTER @source_key == null OR doc.source_key == @source_key
              LIMIT @offset, @batch_size
              RETURN {
                _id: chunk._id,
                _key: chunk._key,
                document_key: chunk.document_key,
                text: chunk.text,
                embedding: chunk.embedding
              }
            """,
            {"dimension": dimension, "source_key": source_key, "model": model, "offset": offset, "batch_size": batch_size},
        )
        if not batch:
            return chunks
        chunks.extend(batch)
        if len(batch) < batch_size:
            return chunks
        offset += batch_size


def _vector_ranked(
    repository: KnowledgeRepository,
    query_vector: list[float],
    *,
    limit: int,
    source_key: str | None,
    model: str,
) -> list[dict[str, Any]] | None:
    """ANN ranking via the chunks vector index (findings #9, #12).

    Returns the best chunk per document ordered by cosine similarity, or None to signal
    that the caller should fall back to the full-scan path. The index is built for the
    configured embedding dimension, so it serves any dimension; only a source filter (which
    the index cannot be combined with) or an index error force the fallback.

    The vector index is shared by every embedding model of the same dimension, so candidates are
    filtered to `model` before dedup: chunks written by an incompatible same-dimension provider
    (e.g. after a partial re-ingest with a different model) must not leak into results (GR-2 review).
    """
    if source_key is not None:
        return None
    # Grow the ANN candidate window until we have `limit` distinct documents or the index
    # has returned everything it holds, so one long document contributing many near chunks
    # cannot starve the result of other documents (PR #8 review, finding #14).
    candidates_limit = max(limit * 10, 50)
    deduped: list[dict[str, Any]] = []
    for _ in range(8):
        try:
            candidates = repository.client.aql(
                """
                FOR chunk IN chunks
                  LET score = APPROX_NEAR_COSINE(chunk.embedding, @query)
                  SORT score DESC
                  LIMIT @candidates
                  RETURN {
                    id: chunk._id,
                    key: chunk._key,
                    document_key: chunk.document_key,
                    text: chunk.text,
                    score: score,
                    embedding_model: chunk.embedding_model
                  }
                """,
                {"query": query_vector, "candidates": candidates_limit},
            )
        except ArangoError:
            return None
        if not candidates:
            return None
        matching = [row for row in candidates if row.get("embedding_model") == model]
        deduped = _dedup_best_by_document(matching)
        # Enough distinct documents, or the index returned fewer rows than asked (exhausted).
        if len(deduped) >= limit or len(candidates) < candidates_limit:
            return deduped
        candidates_limit *= 4
    return deduped


def _dedup_best_by_document(scored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Keep the single best-scoring chunk per document (input must be sorted by score
    # descending) so one document does not occupy several result slots (finding #14).
    best: dict[str, dict[str, Any]] = {}
    for item in scored:
        best.setdefault(item["document_key"], item)
    return list(best.values())


def _hydrate_semantic(repository: KnowledgeRepository, ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Hydrate all ranked chunks with document + provenance in a single query instead of
    # one round-trip per result (finding #17). Order of @items is preserved.
    if not ranked:
        return []
    return repository.client.aql(
        """
        FOR item IN @items
          LET doc = DOCUMENT("documents", item.document_key)
          LET raw_edge = FIRST(FOR e IN chunk_derived_from_raw FILTER e._from == item.id LIMIT 1 RETURN e)
          LET raw = raw_edge ? DOCUMENT(raw_edge._to) : null
          LET source_edge = doc ? FIRST(FOR e IN document_from_source FILTER e._from == doc._id RETURN e) : null
          RETURN {
            id: item.id,
            document_key: doc ? doc._key : item.document_key,
            chunk_key: item.key,
            title: doc.title,
            snippet: SUBSTRING(item.text, 0, 240),
            score: item.score,
            score_components: { bm25: null, vector: item.score, graph_boost: null },
            provenance: {
              source_key: doc.source_key,
              raw_snapshot_key: raw ? raw._key : null,
              import_run_key: raw_edge ? raw_edge.import_run_key : (source_edge ? source_edge.import_run_key : null),
              medium_post: (
                source_edge != null AND HAS(source_edge, "provenance") AND HAS(source_edge.provenance, "medium_post")
                  ? source_edge.provenance.medium_post
                  : null
              ),
              url: doc.url,
              captured_at: raw ? raw.captured_at : null
            }
          }
        """,
        {"items": ranked},
    )


def graph_neighbors(
    repository: KnowledgeRepository,
    *,
    topic: str | None = None,
    author: str | None = None,
    work: str | None = None,
    document: str | None = None,
    chunk: str | None = None,
    limit: int = 10,
    source_key: str | None = None,
    documents_only: bool = False,
) -> dict[str, Any]:
    start = _start_vertex(topic=topic, author=author, work=work, document=document, chunk=chunk)
    if start is None:
        return {"query": "", "mode": "graph", "status": "error", "results": [], "error": "missing start vertex"}

    if documents_only:
        return _graph_document_neighbors(repository, start=start, limit=limit, source_key=source_key)

    template = """
        LET start = DOCUMENT(@start)
        FILTER start != null
        FOR vertex, edge, path IN 1..2 ANY start GRAPH "knowledge_graph"
          __OPTIONS__
          LET kind = IS_SAME_COLLECTION("documents", vertex) ? "document" : (
            IS_SAME_COLLECTION("chunks", vertex) ? "chunk" : (
              IS_SAME_COLLECTION("topics", vertex) ? "topic" : (
                IS_SAME_COLLECTION("authors", vertex) ? "author" : (
                  IS_SAME_COLLECTION("works", vertex) ? "work" : (
                    IS_SAME_COLLECTION("sources", vertex) ? "source" : "item"
                  )
                )
              )
            )
          )
          LET path_doc = FIRST(FOR node IN path.vertices FILTER IS_SAME_COLLECTION("documents", node) RETURN node)
          LET path_chunk = FIRST(FOR node IN path.vertices FILTER IS_SAME_COLLECTION("chunks", node) RETURN node)
          LET doc = IS_SAME_COLLECTION("documents", vertex) ? vertex : (
            IS_SAME_COLLECTION("chunks", vertex) ? DOCUMENT("documents", vertex.document_key) : (
              path_doc ? path_doc : (path_chunk ? DOCUMENT("documents", path_chunk.document_key) : null)
            )
          )
          FILTER doc != null
          __SOURCE_STAGE__
          LET anchor_chunk = IS_SAME_COLLECTION("chunks", vertex) ? vertex : path_chunk
          LET raw_edge = anchor_chunk ? FIRST(FOR e IN chunk_derived_from_raw FILTER e._from == anchor_chunk._id RETURN e) : (
            FIRST(
              FOR chunk_doc IN chunks
                FILTER chunk_doc.document_key == doc._key
                FOR e IN chunk_derived_from_raw
                  FILTER e._from == chunk_doc._id
                  LIMIT 1
                  RETURN e
            )
          )
          LET raw = raw_edge ? DOCUMENT(raw_edge._to) : null
          LET source_edge = FIRST(FOR e IN document_from_source FILTER e._from == doc._id RETURN e)
          LET import_run_key = raw_edge ? raw_edge.import_run_key : (
            edge != null AND HAS(edge, "import_run_key") ? edge.import_run_key : (
              source_edge ? source_edge.import_run_key : null
            )
          )
          LET title = kind == "document" ? vertex.title : (
            kind == "chunk" ? doc.title : (
              kind == "topic" ? vertex.label : (
                kind == "author" ? vertex.display_name : (
                  kind == "work" ? vertex.title : (
                    kind == "source" ? vertex.display_name : vertex._key
                  )
                )
              )
            )
          )
          LET snippet = kind == "document" ? SUBSTRING(vertex.text, 0, 240) : (
            kind == "chunk" ? SUBSTRING(vertex.text, 0, 240) : (
              kind == "topic" ? SUBSTRING(vertex.description, 0, 240) : null
            )
          )
          RETURN {
            id: vertex._id,
            kind: kind,
            entity_key: vertex._key,
            document_key: doc._key,
            chunk_key: kind == "chunk" ? vertex._key : null,
            title: title,
            label: title,
            snippet: snippet,
            score: 1,
            score_components: { bm25: null, vector: null, graph_boost: 1 },
            relation: edge ? edge._id : null,
            provenance: {
              source_key: doc.source_key,
              raw_snapshot_key: raw ? raw._key : null,
              import_run_key: import_run_key,
              medium_post: (
                source_edge != null AND HAS(source_edge, "provenance") AND HAS(source_edge.provenance, "medium_post")
                  ? source_edge.provenance.medium_post
                  : null
              ),
              url: doc.url,
              captured_at: raw ? raw.captured_at : null
            }
          }
        """
    if source_key is None:
        # No source filter: global vertex uniqueness returns each neighbor once and lets
        # the traversal stop at `limit` without enumerating the whole neighborhood, which
        # keeps hub vertices fast (findings #13, #18).
        options = 'OPTIONS { order: "bfs", uniqueVertices: "global", uniqueEdges: "path" }'
        source_stage = "LIMIT @limit"
        bind_vars: dict[str, Any] = {"start": start, "limit": limit}
    else:
        # Source-scoped: a vertex shared across sources must survive the filter even when
        # first reached via another source's document, so paths are enumerated and
        # deduplicated AFTER the source filter rather than during the traversal
        # (finding #18 / PR #7 review). The filter keeps this set small.
        options = 'OPTIONS { uniqueEdges: "path" }'
        source_stage = """
          FILTER doc.source_key == @source_key
          COLLECT vertex_id = vertex._id AGGREGATE depth = MIN(LENGTH(path.edges)) INTO group = {
            vertex: vertex, edge: edge, doc: doc, kind: kind, path_chunk: path_chunk
          }
          SORT depth ASC, vertex_id ASC
          LIMIT @limit
          LET rep = FIRST(group)
          LET vertex = rep.vertex
          LET edge = rep.edge
          LET doc = rep.doc
          LET kind = rep.kind
          LET path_chunk = rep.path_chunk
        """
        bind_vars = {"start": start, "limit": limit, "source_key": source_key}
    query = template.replace("__OPTIONS__", options).replace("__SOURCE_STAGE__", source_stage)
    rows = repository.client.aql(query, bind_vars)
    return {"query": start, "mode": "graph", "status": "ok", "results": rows}


def _graph_document_neighbors(
    repository: KnowledgeRepository,
    *,
    start: str,
    limit: int,
    source_key: str | None,
) -> dict[str, Any]:
    rows = repository.client.aql(
        """
        LET start = DOCUMENT(@start)
        FILTER start != null
        FOR vertex, edge, path IN 1..2 ANY start GRAPH "knowledge_graph"
          LET path_doc = FIRST(FOR node IN path.vertices FILTER IS_SAME_COLLECTION("documents", node) RETURN node)
          LET path_chunk = FIRST(FOR node IN path.vertices FILTER IS_SAME_COLLECTION("chunks", node) RETURN node)
          LET doc = IS_SAME_COLLECTION("documents", vertex) ? vertex : (
            IS_SAME_COLLECTION("chunks", vertex) ? DOCUMENT("documents", vertex.document_key) : (
              path_doc ? path_doc : (path_chunk ? DOCUMENT("documents", path_chunk.document_key) : null)
            )
          )
          FILTER doc != null
          FILTER @source_key == null OR doc.source_key == @source_key
          COLLECT doc_key = doc._key INTO matches = { doc: doc, edge: edge }
          LET doc = FIRST(matches).doc
          SORT doc.published_at DESC, doc.title ASC
          LIMIT @limit
          LET anchor_chunk = FIRST(
            FOR chunk_doc IN chunks
              FILTER chunk_doc.document_key == doc._key
              SORT chunk_doc.ordinal ASC
              LIMIT 1
              RETURN chunk_doc
          )
          LET raw_edge = anchor_chunk ? FIRST(FOR e IN chunk_derived_from_raw FILTER e._from == anchor_chunk._id RETURN e) : null
          LET raw = raw_edge ? DOCUMENT(raw_edge._to) : null
          LET source_edge = FIRST(FOR e IN document_from_source FILTER e._from == doc._id RETURN e)
          RETURN {
            id: doc._id,
            kind: "document",
            entity_key: doc._key,
            document_key: doc._key,
            chunk_key: null,
            title: doc.title,
            label: doc.title,
            snippet: SUBSTRING(doc.text, 0, 240),
            score: 1,
            score_components: { bm25: null, vector: null, graph_boost: 1 },
            relation: null,
            provenance: {
              source_key: doc.source_key,
              raw_snapshot_key: raw ? raw._key : (
                source_edge != null AND HAS(source_edge, "provenance") ? source_edge.provenance.raw_snapshot_key : null
              ),
              import_run_key: raw_edge ? raw_edge.import_run_key : (
                source_edge ? source_edge.import_run_key : null
              ),
              medium_post: (
                source_edge != null AND HAS(source_edge, "provenance") AND HAS(source_edge.provenance, "medium_post")
                  ? source_edge.provenance.medium_post
                  : null
              ),
              url: doc.url,
              captured_at: raw ? raw.captured_at : null
            }
          }
        """,
        {"start": start, "limit": limit, "source_key": source_key},
    )
    return {"query": start, "mode": "graph", "status": "ok", "results": rows}


def hybrid_search(
    repository: KnowledgeRepository,
    query: str,
    *,
    limit: int = 10,
    dimension: int = VECTOR_DIMENSION,
    source_key: str | None = None,
    provider: EmbeddingProvider | None = None,
) -> dict[str, Any]:
    degraded_components: list[str] = []
    try:
        text_results = text_search(repository, query, limit=limit, source_key=source_key)["results"]
    except ArangoError:
        text_results = []
        degraded_components.append("text")

    semantic = semantic_search(repository, query, limit=limit, dimension=dimension, source_key=source_key, provider=provider)
    if semantic["status"] == "degraded":
        degraded_components.append("vector")

    # Fuse text+vector, then fold a graph signal into the score of every retrieved candidate before
    # truncating to `limit`. The signal combines shared topic/author/work entities (GR-1) and direct
    # item_related_to_item similarity links (GR-3b). It re-ranks and boosts the retrieved pool; it
    # does not yet pull in graph-only neighbours that had no text/vector hit — that candidate
    # expansion is the next step, GR-3c (see docs/graphrag-plan.md).
    fused = _fuse_by_document(text_results, semantic["results"])

    if fused:
        try:
            document_keys = [row["document_key"] for row in fused]
            entity_sets = _document_entity_sets(repository, document_keys)
            related = _document_related(repository, document_keys)
        except ArangoError:
            degraded_components.append("graph")
        else:
            boosts = _graph_boosts(fused, entity_sets, related=related)
            for row in fused:
                boost = boosts.get(row["document_key"], 0.0)
                row["score_components"]["graph_boost"] = boost
                row["score"] = round(float(row["score"]) + boost, 6)
            fused.sort(key=lambda item: item["score"], reverse=True)
    # When the graph lookup degrades, graph_boost is left null (from _fuse_by_document) and the
    # ranking falls back to the text+vector order rather than a fabricated signal.

    return {
        "query": query,
        "mode": "hybrid",
        "status": "degraded" if degraded_components else "ok",
        "degraded_components": degraded_components,
        "results": fused[:limit],
    }


def _fuse_by_document(
    text_results: list[dict[str, Any]],
    semantic_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fuse text (BM25) and semantic (cosine) hits into one ranked list, one row per document.

    Aggregates by document_key so a single document never occupies several slots
    and its text/semantic hits combine instead of competing (finding #14). BM25 is
    min-max normalized to [0, 1] over the result set and cosine is clamped to [0, 1],
    so a negative cosine contributes 0 rather than dragging the combined score below
    text-only hits (finding #16). graph_boost is left null here; the graph signal is
    applied downstream by hybrid_search over the full candidate pool (GR-1).
    """
    records: dict[str, dict[str, Any]] = {}
    _index_text_hits(records, text_results)
    _index_semantic_hits(records, semantic_results)

    bm25_values = [record["bm25"] for record in records.values() if record["bm25"] is not None]
    low = min(bm25_values) if bm25_values else 0.0
    high = max(bm25_values) if bm25_values else 0.0

    fused: list[dict[str, Any]] = []
    for document_key_value, record in records.items():
        norm_vector = max(0.0, record["vector"]) if record["vector"] is not None else 0.0
        row = dict(record["row"])
        row["document_key"] = document_key_value
        row["score_components"] = {"bm25": record["bm25"], "vector": record["vector"], "graph_boost": None}
        row["score"] = round(_normalized_text(record["bm25"], low, high) + norm_vector, 6)
        fused.append(row)

    fused.sort(key=lambda item: item["score"], reverse=True)
    return fused


def _index_text_hits(records: dict[str, dict[str, Any]], text_results: list[dict[str, Any]]) -> None:
    """Fold BM25 hits into per-document records, keeping the highest-BM25 representative row."""
    for result in text_results:
        document_key_value = result["document_key"]
        raw = result["score_components"].get("bm25")
        bm25 = float(raw) if raw is not None else float(result["score"])
        record = records.setdefault(document_key_value, {"row": result, "bm25": None, "vector": None})
        if record["bm25"] is None or bm25 > record["bm25"]:
            record["bm25"] = bm25
            record["row"] = result


def _index_semantic_hits(records: dict[str, dict[str, Any]], semantic_results: list[dict[str, Any]]) -> None:
    """Fold cosine hits into per-document records; a document with a text hit keeps that row."""
    for result in semantic_results:
        document_key_value = result["document_key"]
        raw = result["score_components"].get("vector")
        vector = float(raw) if raw is not None else float(result["score"])
        record = records.setdefault(document_key_value, {"row": result, "bm25": None, "vector": None})
        if record["vector"] is None or vector > record["vector"]:
            record["vector"] = vector
            if record["bm25"] is None:
                record["row"] = result


def _normalized_text(bm25: float | None, low: float, high: float) -> float:
    """Min-max normalize BM25 into [0, 1]; a document without a text hit contributes 0."""
    if bm25 is None:
        return 0.0
    if high <= low:
        return 1.0
    return (bm25 - low) / (high - low)


def _merge_hybrid(
    text_results: list[dict[str, Any]],
    semantic_results: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Fuse text (BM25) and semantic (cosine) hits and truncate to `limit`.

    Pure text+vector path with no graph signal (graph_boost stays null); hybrid_search
    layers the graph boost on top before truncating (GR-1).
    """
    return _fuse_by_document(text_results, semantic_results)[:limit]


def _document_entity_sets(
    repository: KnowledgeRepository,
    document_keys: list[str],
) -> dict[str, set[str]]:
    """Entities (topics/authors/works) linked to each document or its chunks (GR-1).

    One AQL over the mention/reference edge collections, matching edges that originate from
    the document node or any of its chunks. Used to boost documents that share entities with
    the strongest candidates of a query.
    """
    unique_keys = list(dict.fromkeys(document_keys))
    if not unique_keys:
        return {}
    rows = repository.client.aql(
        """
        FOR doc_key IN @document_keys
          LET from_ids = PUSH(
            (FOR c IN chunks FILTER c.document_key == doc_key RETURN c._id),
            CONCAT("documents/", doc_key)
          )
          LET topics = (FOR e IN document_mentions_topic FILTER e._from IN from_ids RETURN e._to)
          LET authors = (FOR e IN document_mentions_author FILTER e._from IN from_ids RETURN e._to)
          LET works = (FOR e IN document_references_work FILTER e._from IN from_ids RETURN e._to)
          RETURN { document_key: doc_key, entities: UNION_DISTINCT(topics, authors, works) }
        """,
        {"document_keys": unique_keys},
    )
    return {row["document_key"]: set(row["entities"]) for row in rows}


def _document_related(
    repository: KnowledgeRepository,
    document_keys: list[str],
) -> dict[str, dict[str, float]]:
    """Documents each document is similarity-linked to via item_related_to_item (GR-3b).

    The edges are chunk↔chunk (built by GR-3); this resolves them to the document level and keeps
    the strongest weight per related document, so a similarity link to a strong candidate can lift
    a document in `graph_boost` the same way a shared entity does.
    """
    unique_keys = list(dict.fromkeys(document_keys))
    if not unique_keys:
        return {}
    rows = repository.client.aql(
        """
        FOR doc_key IN @document_keys
          LET chunk_ids = (FOR c IN chunks FILTER c.document_key == doc_key RETURN c._id)
          LET links = (
            FOR e IN item_related_to_item
              FILTER e._from IN chunk_ids OR e._to IN chunk_ids
              LET other = DOCUMENT(e._from IN chunk_ids ? e._to : e._from)
              FILTER other != null AND other.document_key != doc_key
              RETURN { doc: other.document_key, weight: e.weight }
          )
          RETURN { document_key: doc_key, links: links }
        """,
        {"document_keys": unique_keys},
    )
    related: dict[str, dict[str, float]] = {}
    for row in rows:
        best: dict[str, float] = {}
        for link in row["links"]:
            other_doc = link["doc"]
            weight = float(link["weight"])
            if other_doc not in best or weight > best[other_doc]:
                best[other_doc] = weight
        related[row["document_key"]] = best
    return related


def _graph_boosts(
    fused: list[dict[str, Any]],
    entity_sets: dict[str, set[str]],
    *,
    related: dict[str, dict[str, float]] | None = None,
    seed_count: int = _HYBRID_SEED_COUNT,
    cap: float = _GRAPH_BOOST_CAP,
) -> dict[str, float]:
    """Boost documents connected to the query's strongest candidates in the graph (GR-1, GR-3b).

    Seeds are the top `seed_count` documents by fused (text+vector) score. A candidate's raw boost
    sums, over the OTHER seeds, two connection signals: ``seed_score * |shared entities|`` (shared
    topic/author/work — a two-hop neighbourhood) and ``seed_score * similarity_weight`` (a direct
    item_related_to_item link, GR-3b). Raw boosts are min-max scaled to [0, cap] so the graph
    signal reinforces but never dominates the lexical/semantic score; a document with no graph
    connection to any seed gets exactly 0.
    """
    related = related or {}
    base = {row["document_key"]: float(row["score"]) for row in fused}
    if not base:
        return {}
    seeds = sorted(base, key=lambda key: base[key], reverse=True)[:seed_count]
    raw: dict[str, float] = {}
    for document_key_value in base:
        entities: set[str] | frozenset[str] = entity_sets.get(document_key_value) or frozenset()
        links: dict[str, float] = related.get(document_key_value) or {}
        total = 0.0
        for seed in seeds:
            if seed == document_key_value:
                continue
            shared = entities & (entity_sets.get(seed) or frozenset())
            if shared:
                total += base[seed] * len(shared)
            link_weight = links.get(seed)
            if link_weight:
                total += base[seed] * link_weight
        raw[document_key_value] = total
    highest = max(raw.values())
    if highest <= 0.0:
        return dict.fromkeys(base, 0.0)
    return {key: round(cap * value / highest, 6) for key, value in raw.items()}


def _start_vertex(
    *,
    topic: str | None,
    author: str | None,
    work: str | None,
    document: str | None,
    chunk: str | None,
) -> str | None:
    if topic:
        return f"topics/{topic}"
    if author:
        return f"authors/{author}"
    if work:
        return f"works/{work}"
    if document:
        return f"documents/{document}"
    if chunk:
        return f"chunks/{chunk}"
    return None


# Kept as a module-level name for internal use and the existing unit tests; the implementation
# now lives in embeddings so the similarity-edge builder (GR-3) can share it.
_cosine = cosine_similarity
