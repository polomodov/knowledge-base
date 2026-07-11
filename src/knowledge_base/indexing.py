from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from knowledge_base.config import Settings, load_settings
from knowledge_base.constants import RELATED_EDGE_METHOD, RELATED_MIN_SCORE, RELATED_TOP_K, VECTOR_DIMENSION
from knowledge_base.embeddings import EmbeddingProvider, build_embedding_provider, cosine_similarity
from knowledge_base.ids import stable_key
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.schema import bootstrap_schema, ensure_vector_index

_INDEX_TARGETS = {"all", "text", "vector", "graph", "related", "embeddings"}


def rebuild_indexes(
    repository: KnowledgeRepository,
    *,
    target: str = "all",
    embedding_dimension: int = VECTOR_DIMENSION,
    settings: Settings | None = None,
) -> dict[str, Any]:
    if target not in _INDEX_TARGETS:
        raise ValueError(f"Invalid index target: {target}")

    started = _now()
    index_run_key = stable_key("index", target, started, prefix="index")
    run: dict[str, Any] = {
        "_key": index_run_key,
        "started_at": started,
        "finished_at": None,
        "status": "running",
        "target": target,
        "counts": {},
        "error": None,
    }
    repository.upsert("index_runs", run)
    bootstrap = bootstrap_schema(repository.client, embedding_dimension=embedding_dimension)
    counts = {
        "documents": repository.count("documents"),
        "chunks": repository.count("chunks"),
        "text_indexed": repository.count("chunks") if target in {"all", "text"} else 0,
        "vectors_indexed": _count_chunks_with_embeddings(repository) if target in {"all", "vector"} else 0,
        "graph_edges_checked": sum(repository.count(edge) for edge in _graph_edges()) if target in {"all", "graph"} else 0,
    }
    # Related-edge building is its own explicit target, not part of "all": it is O(N^2) over the
    # corpus and mutates the graph, so it should be run deliberately (`index rebuild --target related`),
    # not implicitly on every rebuild.
    if target == "related":
        related = build_related_edges(repository)
        counts["related_pairs"] = related["pairs"]
        counts["related_edges_created"] = related["created"]
        counts["related_edges_removed"] = related["removed"]
    # Re-embedding is its own explicit target (never part of "all"): it rewrites every chunk vector
    # and the vector index, which is how you switch embedding providers/models (`--target embeddings`).
    if target == "embeddings":
        embedded = build_embeddings(repository, settings or load_settings())
        counts["chunks_reembedded"] = embedded["chunks"]
        counts["embedding_model"] = embedded["model"]
        counts["embedding_dimension"] = embedded["dimension"]
    run["finished_at"] = _now()
    run["status"] = "ok"
    run["counts"] = counts
    repository.upsert("index_runs", run)
    return {"status": "ok", "index_run_key": index_run_key, "target": target, "counts": counts, "bootstrap": bootstrap}


def build_embeddings(repository: KnowledgeRepository, settings: Settings) -> dict[str, Any]:
    """Re-embed every chunk with the configured provider and rebuild the vector index.

    This is how you switch embedding providers/models after ingest without re-running the source
    adapters: it recomputes each chunk's vector (and stored `embedding_model`) with the current
    provider. The vector index is dropped first because its dimension is fixed at creation — a new
    provider may use a different dimension — then recreated at the provider's dimension once the
    chunks carry vectors of the new size.
    """
    provider = build_embedding_provider(settings)
    repository.client.drop_index("chunks", "idx_chunks_embedding_vector")
    reembedded = _reembed_chunks(repository, provider)
    index = ensure_vector_index(repository.client, dimension=provider.dimension)
    return {"chunks": reembedded, "model": provider.model, "dimension": provider.dimension, "vector_index": index}


def _reembed_chunks(repository: KnowledgeRepository, provider: EmbeddingProvider, *, batch_size: int = 500) -> int:
    total = 0
    offset = 0
    while True:
        chunks = repository.client.aql(
            "FOR c IN chunks SORT c._key LIMIT @offset, @batch_size RETURN { key: c._key, text: c.text }",
            {"offset": offset, "batch_size": batch_size},
        )
        if not chunks:
            return total
        updates = [{"key": row["key"], "embedding": provider.embed(row["text"]), "model": provider.model} for row in chunks]
        repository.client.aql(
            "FOR item IN @items UPDATE item.key WITH { embedding: item.embedding, embedding_model: item.model } IN chunks",
            {"items": updates},
        )
        total += len(chunks)
        if len(chunks) < batch_size:
            return total
        offset += batch_size


def build_related_edges(
    repository: KnowledgeRepository,
    *,
    top_k: int = RELATED_TOP_K,
    min_score: float = RELATED_MIN_SCORE,
    source_key: str | None = None,
) -> dict[str, Any]:
    """Populate item_related_to_item with cross-document embedding-similarity edges (GR-3).

    Turns the provenance tree into a knowledge graph: each chunk is linked to its most similar
    chunks from OTHER documents. Edges are undirected (one per unordered pair, deterministic key)
    and carry the cosine `weight`, so the write is idempotent — re-running only adds new links.

    Without `source_key` the neighbours come from the ANN vector index (`APPROX_NEAR_COSINE`), so
    it scales across the whole corpus. With `source_key` the search is scoped to that source's
    chunks and compared directly (the vector index cannot be combined with a filter), which keeps
    a scoped rebuild — and tests — fast and isolated from the rest of the corpus.

    It is a rebuildable derived index: the edges it owns are cleared first and rewritten from the
    current embeddings/threshold, so stale weights and links that dropped below the threshold do
    not survive a rebuild.
    """
    chunks = _chunks_for_similarity(repository, source_key=source_key)
    pairs: dict[tuple[str, str], float] = {}
    for chunk in chunks:
        if source_key is None:
            selected = _ann_related(repository, chunk, top_k=top_k, min_score=min_score)
        else:
            selected = _select_related(chunk, _scored_candidates(chunk, chunks), top_k=top_k, min_score=min_score)
        for other_id, weight in selected:
            key = (chunk["id"], other_id) if chunk["id"] < other_id else (other_id, chunk["id"])
            if key not in pairs or weight > pairs[key]:
                pairs[key] = weight

    # Clear the edges this build owns, then insert fresh, so a rebuild reflects current embeddings.
    removed = _clear_related_edges(repository, [chunk["id"] for chunk in chunks], scoped=source_key is not None)
    if not pairs:
        return {"chunks": len(chunks), "pairs": 0, "created": 0, "removed": removed}
    now = _now()
    edges = [
        {
            "_key": stable_key(from_id, to_id, prefix="rel"),
            "_from": from_id,
            "_to": to_id,
            "weight": weight,
            "method": RELATED_EDGE_METHOD,
            "created_at": now,
        }
        for (from_id, to_id), weight in sorted(pairs.items())
    ]
    repository.client.aql(
        "FOR edge IN @edges INSERT edge INTO item_related_to_item",
        {"edges": edges},
    )
    return {"chunks": len(chunks), "pairs": len(pairs), "created": len(edges), "removed": removed}


def _ann_related(
    repository: KnowledgeRepository,
    chunk: dict[str, Any],
    *,
    top_k: int,
    min_score: float,
) -> list[tuple[str, float]]:
    """Top-`top_k` valid neighbours for `chunk` via the ANN index, growing the window.

    Self / same-document / incompatible-model rows are dropped only after the ANN returns them, so
    a fixed window can be entirely invalid for a long document or a mixed-model corpus. The window
    grows until `top_k` valid neighbours are found or the index is exhausted (as `_vector_ranked`
    does), instead of silently returning nothing.
    """
    window = max(top_k * 10, 50)
    selected: list[tuple[str, float]] = []
    for _ in range(8):
        candidates = _ann_candidates(repository, chunk["embedding"], window=window)
        selected = _select_related(chunk, candidates, top_k=top_k, min_score=min_score)
        if len(selected) >= top_k or len(candidates) < window:
            return selected
        window *= 4
    return selected


def _clear_related_edges(repository: KnowledgeRepository, chunk_ids: list[str], *, scoped: bool) -> int:
    """Remove the embedding-similarity edges this build owns; returns how many were removed.

    A scoped build owns only within-source edges (both endpoints among `chunk_ids`); a full build
    owns every embedding-similarity edge. Non-derived edges (other methods) are never touched.
    """
    if scoped:
        return int(
            repository.client.aql(
                """
                RETURN LENGTH(
                  FOR e IN item_related_to_item
                    FILTER e.method == @method AND e._from IN @ids AND e._to IN @ids
                    REMOVE e IN item_related_to_item
                    RETURN 1
                )
                """,
                {"ids": chunk_ids, "method": RELATED_EDGE_METHOD},
            )[0]
        )
    return int(
        repository.client.aql(
            """
            RETURN LENGTH(
              FOR e IN item_related_to_item
                FILTER e.method == @method
                REMOVE e IN item_related_to_item
                RETURN 1
            )
            """,
            {"method": RELATED_EDGE_METHOD},
        )[0]
    )


def _select_related(
    chunk: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    top_k: int,
    min_score: float,
) -> list[tuple[str, float]]:
    """Pure: keep a chunk's top-`top_k` valid neighbours from a scored candidate list.

    A neighbour is valid when it is a different chunk, from a different document, in the SAME
    embedding model (a same-dimension but different model lives in an incompatible vector space —
    see GR-2), and scores at least `min_score`. Ties break by id so the result is deterministic.
    """
    neighbours: list[tuple[float, str]] = []
    for candidate in candidates:
        if candidate["id"] == chunk["id"] or candidate["document_key"] == chunk["document_key"]:
            continue
        if candidate["embedding_model"] != chunk["embedding_model"]:
            continue
        if candidate["score"] >= min_score:
            neighbours.append((candidate["score"], candidate["id"]))
    neighbours.sort(key=lambda item: (-item[0], item[1]))
    return [(candidate_id, score) for score, candidate_id in neighbours[:top_k]]


def _scored_candidates(chunk: dict[str, Any], pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score every other chunk in `pool` against `chunk` by cosine (scoped, small-N path)."""
    return [
        {
            "id": other["id"],
            "document_key": other["document_key"],
            "embedding_model": other["embedding_model"],
            "score": cosine_similarity(chunk["embedding"], other["embedding"]),
        }
        for other in pool
        if other["id"] != chunk["id"]
    ]


def _ann_candidates(repository: KnowledgeRepository, embedding: list[float], *, window: int) -> list[dict[str, Any]]:
    """Nearest chunks to `embedding` via the ANN vector index (whole-corpus, scalable path)."""
    return repository.client.aql(
        """
        FOR chunk IN chunks
          LET score = APPROX_NEAR_COSINE(chunk.embedding, @query)
          SORT score DESC
          LIMIT @window
          RETURN { id: chunk._id, document_key: chunk.document_key, embedding_model: chunk.embedding_model, score: score }
        """,
        {"query": embedding, "window": window},
    )


def _chunks_for_similarity(repository: KnowledgeRepository, *, source_key: str | None = None) -> list[dict[str, Any]]:
    return repository.client.aql(
        """
        FOR chunk IN chunks
          FILTER HAS(chunk, "embedding")
          LET doc = DOCUMENT("documents", chunk.document_key)
          FILTER @source_key == null OR (doc != null AND doc.source_key == @source_key)
          SORT chunk._id ASC
          RETURN {
            id: chunk._id,
            document_key: chunk.document_key,
            embedding: chunk.embedding,
            embedding_model: chunk.embedding_model
          }
        """,
        {"source_key": source_key},
    )


def _count_chunks_with_embeddings(repository: KnowledgeRepository) -> int:
    result = repository.client.aql("RETURN LENGTH(FOR chunk IN chunks FILTER HAS(chunk, 'embedding') RETURN 1)")
    return int(result[0])


def _graph_edges() -> list[str]:
    return [
        "document_from_source",
        "chunk_of_document",
        "document_mentions_topic",
        "document_mentions_author",
        "document_references_work",
        "chunk_derived_from_raw",
        "item_related_to_item",
    ]


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
