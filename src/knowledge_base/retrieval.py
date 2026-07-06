from __future__ import annotations

import math
from typing import Any

from knowledge_base.arango import ArangoError
from knowledge_base.embeddings import hash_embedding
from knowledge_base.repository import KnowledgeRepository


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
          SORT score DESC
          LIMIT @limit
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
    dimension: int = 8,
    source_key: str | None = None,
) -> dict[str, Any]:
    query_vector = hash_embedding(query, dimension=dimension)
    chunks = _semantic_candidate_chunks(repository, dimension=dimension, source_key=source_key)
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

    results = []
    for chunk in chunks:
        score = _cosine(query_vector, chunk["embedding"])
        results.append({"chunk": chunk, "score": score})
    results.sort(key=lambda result: result["score"], reverse=True)
    hydrated = [_semantic_result_for_chunk(repository, result["chunk"], result["score"]) for result in results[:limit]]
    return {"query": query, "mode": "semantic", "status": "ok", "results": hydrated}


def _semantic_candidate_chunks(
    repository: KnowledgeRepository,
    *,
    dimension: int,
    source_key: str | None,
    batch_size: int = 500,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    offset = 0
    while True:
        batch = repository.client.aql(
            """
            FOR chunk IN chunks
              FILTER HAS(chunk, "embedding") AND LENGTH(chunk.embedding) == @dimension
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
            {"dimension": dimension, "source_key": source_key, "offset": offset, "batch_size": batch_size},
        )
        if not batch:
            return chunks
        chunks.extend(batch)
        if len(batch) < batch_size:
            return chunks
        offset += batch_size


def _semantic_result_for_chunk(repository: KnowledgeRepository, chunk: dict[str, Any], score: float) -> dict[str, Any]:
    row = repository.client.aql(
        """
        LET doc = DOCUMENT("documents", @document_key)
        LET raw_edge = FIRST(
          FOR e IN chunk_derived_from_raw
            FILTER e._from == @chunk_id
            LIMIT 1
            RETURN e
        )
        LET raw = raw_edge ? DOCUMENT(raw_edge._to) : null
        LET source_edge = doc ? FIRST(FOR e IN document_from_source FILTER e._from == doc._id RETURN e) : null
        RETURN { doc: doc, raw_edge: raw_edge, raw: raw, source_edge: source_edge }
        """,
        {"document_key": chunk["document_key"], "chunk_id": chunk["_id"]},
    )[0]
    doc = row["doc"] or {}
    raw_edge = row["raw_edge"]
    raw = row["raw"]
    source_edge = row["source_edge"]
    return {
        "id": chunk["_id"],
        "document_key": doc.get("_key") or chunk["document_key"],
        "chunk_key": chunk["_key"],
        "title": doc.get("title"),
        "snippet": chunk.get("text", "")[:240],
        "score": score,
        "score_components": {"bm25": None, "vector": score, "graph_boost": None},
        "provenance": {
            "source_key": doc.get("source_key"),
            "raw_snapshot_key": raw.get("_key") if raw else None,
            "import_run_key": raw_edge.get("import_run_key") if raw_edge else None,
            "medium_post": (source_edge.get("provenance") or {}).get("medium_post") if source_edge else None,
            "url": doc.get("url"),
            "captured_at": raw.get("captured_at") if raw else None,
        },
    }


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

    early_limit = "LIMIT @limit" if source_key is None else ""
    filtered_limit = "" if source_key is None else "LIMIT @limit"
    query = """
        LET start = DOCUMENT(@start)
        FILTER start != null
        FOR vertex, edge, path IN 1..2 ANY start GRAPH "knowledge_graph"
          __EARLY_LIMIT__
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
          FILTER @source_key == null OR doc.source_key == @source_key
          __FILTERED_LIMIT__
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
    rows = repository.client.aql(
        query.replace("__EARLY_LIMIT__", early_limit).replace("__FILTERED_LIMIT__", filtered_limit),
        {"start": start, "limit": limit, "source_key": source_key},
    )
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
    dimension: int = 8,
    source_key: str | None = None,
) -> dict[str, Any]:
    degraded_components: list[str] = []
    try:
        text = text_search(repository, query, limit=limit, source_key=source_key)["results"]
    except ArangoError:
        text = []
        degraded_components.append("text")

    semantic = semantic_search(repository, query, limit=limit, dimension=dimension, source_key=source_key)
    if semantic["status"] == "degraded":
        degraded_components.append("vector")

    merged: dict[str, dict[str, Any]] = {}
    for result in text:
        key = result.get("chunk_key") or result["document_key"]
        merged[key] = result
        merged[key]["score"] = _normalize(result["score"])
    for result in semantic["results"]:
        key = result.get("chunk_key") or result["document_key"]
        existing = merged.get(key)
        if existing:
            existing["score_components"]["vector"] = result["score_components"]["vector"]
            existing["score"] += _normalize(result["score"])
        else:
            merged[key] = result
            merged[key]["score"] = _normalize(result["score"])

    tokens = {token.lower() for token in query.split() if token}
    for result in merged.values():
        snippet = (result.get("snippet") or "").lower()
        graph_boost = 0.1 if any(token in snippet for token in tokens) else 0.0
        result["score_components"]["graph_boost"] = graph_boost
        result["score"] += graph_boost

    results = sorted(merged.values(), key=lambda item: item["score"], reverse=True)[:limit]
    return {
        "query": query,
        "mode": "hybrid",
        "status": "degraded" if degraded_components else "ok",
        "degraded_components": degraded_components,
        "results": results,
    }


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


def _cosine(left: list[float], right: list[float]) -> float:
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(sum(value * value for value in right))
    if denominator == 0:
        return 0.0
    return round(sum(a * b for a, b in zip(left, right, strict=False)) / denominator, 6)


def _normalize(value: float | int | None) -> float:
    if value is None:
        return 0.0
    return float(value) / (1.0 + abs(float(value)))
