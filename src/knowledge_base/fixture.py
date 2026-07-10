from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from knowledge_base.chunking import split_text
from knowledge_base.config import REPO_ROOT, Settings
from knowledge_base.embeddings import build_embedding_provider
from knowledge_base.ids import chunk_key, document_key, sha256_text, stable_key
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.schema import bootstrap_schema

FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "safe_knowledge_fixture.json"


def ingest_fixture(repository: KnowledgeRepository, settings: Settings, fixture_path: Path = FIXTURE_PATH) -> dict[str, Any]:
    bootstrap_schema(repository.client, embedding_dimension=settings.embedding_dimension)
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    now = _now()
    import_run_key = stable_key("fixture", fixture_path.name, now[:10], prefix="import")
    counts = _counts()

    source = {
        "_key": fixture["source"]["key"],
        "type": fixture["source"]["type"],
        "display_name": fixture["source"]["display_name"],
        "created_at": now,
        "metadata": fixture["source"].get("metadata", {}),
    }
    counts["sources"] += int(repository.upsert("sources", source)["created"])

    raw_payload = fixture["raw_snapshot"].get("payload", "")
    raw = {
        "_key": fixture["raw_snapshot"]["key"],
        "source_key": source["_key"],
        "sha256": sha256_text(raw_payload),
        "size_bytes": len(raw_payload.encode("utf-8")),
        "media_type": fixture["raw_snapshot"]["media_type"],
        "storage_kind": fixture["raw_snapshot"]["storage_kind"],
        "storage_uri": fixture["raw_snapshot"]["storage_uri"],
        "captured_at": now,
        "payload": raw_payload,
        "metadata": {"fixture": True, "safe_for_git": True},
    }
    counts["raw_snapshots"] += int(repository.upsert("raw_snapshots", raw)["created"])

    import_run = {
        "_key": import_run_key,
        "started_at": now,
        "finished_at": None,
        "status": "running",
        "command": "kb ingest fixture",
        "source_key": source["_key"],
        "input_ref": str(fixture_path.relative_to(settings.repo_root)),
        "counts": {},
        "error": None,
    }
    repository.upsert("import_runs", import_run)

    for item in fixture["documents"]:
        counts = _ingest_document(repository, settings, source, raw, item, import_run_key, now, counts)

    import_run["finished_at"] = _now()
    import_run["status"] = "ok"
    import_run["counts"] = counts
    repository.upsert("import_runs", import_run)

    total_documents = len(fixture["documents"])
    total_chunks = sum(len(split_text(" ".join(item["text"].split()))) for item in fixture["documents"])
    return {
        "status": "ok",
        "import_run_key": import_run_key,
        "created": counts,
        "deduplicated": _deduplicated(total_documents, total_chunks, counts),
    }


def _ingest_document(
    repository: KnowledgeRepository,
    settings: Settings,
    source: dict[str, Any],
    raw: dict[str, Any],
    item: dict[str, Any],
    import_run_key: str,
    now: str,
    counts: dict[str, int],
) -> dict[str, int]:
    doc_key = document_key(source["_key"], item["canonical_id"])
    # Store the whitespace-normalized text so chunk char_start/char_end index
    # faithfully into document.text (finding #36); split_text normalizes the
    # same way, so the stored text and the chunk offsets stay aligned.
    text = " ".join(item["text"].split())
    document = {
        "_key": doc_key,
        "source_key": source["_key"],
        "canonical_id": item["canonical_id"],
        "title": item["title"],
        "text": text,
        "language": item.get("language", "unknown"),
        "published_at": item.get("published_at"),
        "url": item.get("url"),
        "status": item.get("status", "fixture"),
        "metadata": item.get("metadata", {}),
        "created_at": now,
        "updated_at": now,
    }
    counts["documents"] += int(repository.upsert("documents", document)["created"])
    counts["edges"] += int(
        repository.upsert_edge(
            "document_from_source",
            {
                "_key": stable_key(doc_key, source["_key"], prefix="edge"),
                "_from": f"documents/{doc_key}",
                "_to": f"sources/{source['_key']}",
                "import_run_key": import_run_key,
                "provenance": {"url": item.get("url"), "raw_snapshot_key": raw["_key"]},
                "created_at": now,
            },
        )["created"],
    )

    _upsert_topics_authors_works(repository, item, now, counts)
    _upsert_document_entity_edges(repository, item, doc_key, raw["_key"], import_run_key, now, counts)

    provider = build_embedding_provider(settings)
    for chunk in split_text(text):
        c_key = chunk_key(doc_key, chunk.ordinal, chunk.text)
        chunk_doc = {
            "_key": c_key,
            "document_key": doc_key,
            "ordinal": chunk.ordinal,
            "text": chunk.text,
            "token_count": chunk.token_count,
            "char_start": chunk.char_start,
            "char_end": chunk.char_end,
            "embedding": provider.embed(chunk.text),
            "embedding_model": provider.model,
            "metadata": {"fixture": True},
        }
        counts["chunks"] += int(repository.upsert("chunks", chunk_doc)["created"])
        counts["edges"] += int(
            repository.upsert_edge(
                "chunk_of_document",
                {
                    "_key": stable_key(c_key, doc_key, prefix="edge"),
                    "_from": f"chunks/{c_key}",
                    "_to": f"documents/{doc_key}",
                    "ordinal": chunk.ordinal,
                    "created_at": now,
                },
            )["created"],
        )
        counts["edges"] += int(
            repository.upsert_edge(
                "chunk_derived_from_raw",
                {
                    "_key": stable_key(c_key, raw["_key"], prefix="edge"),
                    "_from": f"chunks/{c_key}",
                    "_to": f"raw_snapshots/{raw['_key']}",
                    "document_key": doc_key,
                    "char_start": chunk.char_start,
                    "char_end": chunk.char_end,
                    "import_run_key": import_run_key,
                },
            )["created"],
        )
        _upsert_chunk_topic_edges(repository, item, c_key, now, counts)

    return counts


def _upsert_topics_authors_works(
    repository: KnowledgeRepository,
    item: dict[str, Any],
    now: str,
    counts: dict[str, int],
) -> None:
    for topic in item.get("topics", []):
        counts["topics"] += int(
            repository.upsert(
                "topics",
                {
                    "_key": topic["key"],
                    "label": topic["label"],
                    "language": topic.get("language", "unknown"),
                    "description": topic.get("description", ""),
                    "confidence": topic.get("confidence", 1.0),
                    "metadata": topic.get("metadata", {}),
                },
            )["created"],
        )
    for author in item.get("authors", []):
        counts["authors"] += int(
            repository.upsert(
                "authors",
                {
                    "_key": author["key"],
                    "display_name": author["display_name"],
                    "aliases": author.get("aliases", []),
                    "metadata": author.get("metadata", {}),
                },
            )["created"],
        )
    for work in item.get("works", []):
        counts["works"] += int(
            repository.upsert(
                "works",
                {
                    "_key": work["key"],
                    "title": work["title"],
                    "work_type": work.get("work_type", "unknown"),
                    "authors": work.get("authors", []),
                    "published_at": work.get("published_at"),
                    "metadata": work.get("metadata", {}),
                },
            )["created"],
        )


def _upsert_document_entity_edges(
    repository: KnowledgeRepository,
    item: dict[str, Any],
    doc_key: str,
    raw_key: str,
    import_run_key: str,
    now: str,
    counts: dict[str, int],
) -> None:
    provenance = {"raw_snapshot_key": raw_key, "url": item.get("url")}
    for topic in item.get("topics", []):
        counts["edges"] += int(
            repository.upsert_edge(
                "document_mentions_topic",
                {
                    "_key": stable_key(doc_key, topic["key"], prefix="edge"),
                    "_from": f"documents/{doc_key}",
                    "_to": f"topics/{topic['key']}",
                    "confidence": topic.get("confidence", 1.0),
                    "method": "fixture",
                    "evidence": "safe fixture metadata",
                    "import_run_key": import_run_key,
                    "provenance": provenance,
                    "created_at": now,
                },
            )["created"],
        )
    for author in item.get("authors", []):
        counts["edges"] += int(
            repository.upsert_edge(
                "document_mentions_author",
                {
                    "_key": stable_key(doc_key, author["key"], prefix="edge"),
                    "_from": f"documents/{doc_key}",
                    "_to": f"authors/{author['key']}",
                    "confidence": author.get("confidence", 1.0),
                    "method": "fixture",
                    "evidence": "safe fixture metadata",
                    "import_run_key": import_run_key,
                    "provenance": provenance,
                    "created_at": now,
                },
            )["created"],
        )
    for work in item.get("works", []):
        counts["edges"] += int(
            repository.upsert_edge(
                "document_references_work",
                {
                    "_key": stable_key(doc_key, work["key"], prefix="edge"),
                    "_from": f"documents/{doc_key}",
                    "_to": f"works/{work['key']}",
                    "confidence": work.get("confidence", 1.0),
                    "method": "fixture",
                    "evidence": "safe fixture metadata",
                    "import_run_key": import_run_key,
                    "provenance": provenance,
                    "created_at": now,
                },
            )["created"],
        )


def _upsert_chunk_topic_edges(
    repository: KnowledgeRepository,
    item: dict[str, Any],
    c_key: str,
    now: str,
    counts: dict[str, int],
) -> None:
    for topic in item.get("topics", []):
        counts["edges"] += int(
            repository.upsert_edge(
                "document_mentions_topic",
                {
                    "_key": stable_key(c_key, topic["key"], prefix="edge"),
                    "_from": f"chunks/{c_key}",
                    "_to": f"topics/{topic['key']}",
                    "confidence": topic.get("confidence", 1.0),
                    "method": "fixture",
                    "evidence": "safe fixture metadata",
                    "created_at": now,
                },
            )["created"],
        )


def _counts() -> dict[str, int]:
    return {
        "sources": 0,
        "raw_snapshots": 0,
        "documents": 0,
        "chunks": 0,
        "topics": 0,
        "authors": 0,
        "works": 0,
        "edges": 0,
    }


def _deduplicated(total_documents: int, total_chunks: int, created: dict[str, int]) -> dict[str, int]:
    # Report how many documents/chunks were already present (skipped), computed
    # from real totals rather than a hardcoded 0/1 flag (finding #35).
    return {
        "documents": max(total_documents - created["documents"], 0),
        "chunks": max(total_chunks - created["chunks"], 0),
    }


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
