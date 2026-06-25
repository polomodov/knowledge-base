from __future__ import annotations

import math
from typing import Any

from knowledge_base.arango import ArangoError
from knowledge_base.embeddings import fixture_embedding
from knowledge_base.repository import KnowledgeRepository


def text_search(repository: KnowledgeRepository, query: str, *, limit: int = 10) -> dict[str, Any]:
    rows = repository.client.aql(
        """
        FOR item IN kb_text_view
          SEARCH ANALYZER(item.text IN TOKENS(@query, "text_en") OR item.title IN TOKENS(@query, "text_en"), "text_en")
          FILTER IS_SAME_COLLECTION("documents", item) OR IS_SAME_COLLECTION("chunks", item)
          LET score = BM25(item)
          SORT score DESC
          LIMIT @limit
          LET is_chunk = HAS(item, "document_key")
          LET doc = is_chunk ? DOCUMENT("documents", item.document_key) : item
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
              url: doc.url,
              captured_at: raw ? raw.captured_at : null
            }
          }
        """,
        {"query": query, "limit": limit},
    )
    return {"query": query, "mode": "text", "status": "ok", "results": rows}


def semantic_search(repository: KnowledgeRepository, query: str, *, limit: int = 10, dimension: int = 8) -> dict[str, Any]:
    query_vector = fixture_embedding(query, dimension=dimension)
    chunks = repository.client.aql(
        """
        FOR chunk IN chunks
          FILTER HAS(chunk, "embedding") AND LENGTH(chunk.embedding) == @dimension
          LET doc = DOCUMENT("documents", chunk.document_key)
          LET raw_edge = FIRST(FOR e IN chunk_derived_from_raw FILTER e._from == chunk._id RETURN e)
          LET raw = raw_edge ? DOCUMENT(raw_edge._to) : null
          RETURN { chunk: chunk, doc: doc, raw_edge: raw_edge, raw: raw }
        """,
        {"dimension": dimension},
    )
    if not chunks:
        return {
            "query": query,
            "mode": "semantic",
            "status": "degraded",
            "degraded_components": ["vector"],
            "results": [],
        }

    results = []
    for row in chunks:
        chunk = row["chunk"]
        score = _cosine(query_vector, chunk["embedding"])
        doc = row["doc"]
        raw_edge = row["raw_edge"]
        raw = row["raw"]
        results.append(
            {
                "id": chunk["_id"],
                "document_key": doc["_key"],
                "chunk_key": chunk["_key"],
                "title": doc.get("title"),
                "snippet": chunk.get("text", "")[:240],
                "score": score,
                "score_components": {"bm25": None, "vector": score, "graph_boost": None},
                "provenance": {
                    "source_key": doc.get("source_key"),
                    "raw_snapshot_key": raw.get("_key") if raw else None,
                    "import_run_key": raw_edge.get("import_run_key") if raw_edge else None,
                    "url": doc.get("url"),
                    "captured_at": raw.get("captured_at") if raw else None,
                },
            },
        )
    results.sort(key=lambda result: result["score"], reverse=True)
    return {"query": query, "mode": "semantic", "status": "ok", "results": results[:limit]}


def graph_neighbors(
    repository: KnowledgeRepository,
    *,
    topic: str | None = None,
    author: str | None = None,
    work: str | None = None,
    document: str | None = None,
    chunk: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    start = _start_vertex(topic=topic, author=author, work=work, document=document, chunk=chunk)
    if start is None:
        return {"query": "", "mode": "graph", "status": "error", "results": [], "error": "missing start vertex"}

    rows = repository.client.aql(
        """
        LET start = DOCUMENT(@start)
        FILTER start != null
        FOR vertex, edge, path IN 1..2 ANY start GRAPH "knowledge_graph"
          LIMIT @limit
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
              url: doc.url,
              captured_at: raw ? raw.captured_at : null
            }
          }
        """,
        {"start": start, "limit": limit},
    )
    return {"query": start, "mode": "graph", "status": "ok", "results": rows}


def hybrid_search(repository: KnowledgeRepository, query: str, *, limit: int = 10, dimension: int = 8) -> dict[str, Any]:
    degraded_components: list[str] = []
    try:
        text = text_search(repository, query, limit=limit)["results"]
    except ArangoError:
        text = []
        degraded_components.append("text")

    semantic = semantic_search(repository, query, limit=limit, dimension=dimension)
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
