from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from knowledge_base.ids import stable_key
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.schema import bootstrap_schema


def rebuild_indexes(repository: KnowledgeRepository, *, target: str = "all") -> dict[str, Any]:
    if target not in {"all", "text", "vector", "graph"}:
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
    bootstrap = bootstrap_schema(repository.client)
    counts = {
        "documents": repository.count("documents"),
        "chunks": repository.count("chunks"),
        "text_indexed": repository.count("chunks") if target in {"all", "text"} else 0,
        "vectors_indexed": _count_chunks_with_embeddings(repository) if target in {"all", "vector"} else 0,
        "graph_edges_checked": sum(repository.count(edge) for edge in _graph_edges()) if target in {"all", "graph"} else 0,
    }
    run["finished_at"] = _now()
    run["status"] = "ok"
    run["counts"] = counts
    repository.upsert("index_runs", run)
    return {"status": "ok", "index_run_key": index_run_key, "target": target, "counts": counts, "bootstrap": bootstrap}


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
