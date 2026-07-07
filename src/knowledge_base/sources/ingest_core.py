"""Shared ingest write-layer used by every source adapter.

Adapters fetch and normalize into NormalizedSourceItem, then hand off to these functions
which own all KnowledgeRepository writes (documents, chunks, topics, authors, and the edges
between them). Adapter-specific bits — document/chunk metadata, the topic/author `method`
label and evidence format, and the provenance dict — are passed in as parameters.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from knowledge_base.chunking import split_text
from knowledge_base.config import Settings
from knowledge_base.embeddings import HASH_EMBEDDING_MODEL, hash_embedding
from knowledge_base.ids import chunk_key, document_key, slugify, stable_key, topic_key
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.sources.contracts import NormalizedSourceItem


def empty_counts() -> dict[str, int]:
    return {"sources": 0, "raw_snapshots": 0, "documents": 0, "chunks": 0, "topics": 0, "authors": 0, "works": 0, "edges": 0}


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_date(value: str | None) -> str | None:
    """Normalize an RFC 2822 (RSS pubDate) or ISO-8601 timestamp to UTC; raw value on failure."""
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def planned_chunk_count(items: list[NormalizedSourceItem]) -> int:
    return sum(len(split_text(item.text)) for item in items)


def upsert_document(
    repository: KnowledgeRepository,
    source_key: str,
    item: NormalizedSourceItem,
    import_run_key: str,
    now: str,
    counts: dict[str, int],
    *,
    metadata: dict[str, Any],
    status: str,
    provenance: dict[str, Any],
) -> str:
    doc_key = document_key(source_key, item.canonical_id)
    document = {
        "_key": doc_key,
        "source_key": source_key,
        "canonical_id": item.canonical_id,
        "title": item.title,
        "text": item.text,
        "language": item.language,
        "published_at": item.published_at,
        "url": item.url,
        "status": status,
        "metadata": metadata,
        "created_at": now,
        "updated_at": now,
    }
    counts["documents"] += int(repository.upsert("documents", document)["created"])
    counts["edges"] += int(
        repository.upsert_edge(
            "document_from_source",
            {
                "_key": stable_key(doc_key, source_key, prefix="edge"),
                "_from": f"documents/{doc_key}",
                "_to": f"sources/{source_key}",
                "import_run_key": import_run_key,
                "provenance": provenance,
                "created_at": now,
            },
        )["created"],
    )
    return doc_key


def upsert_topics(
    repository: KnowledgeRepository,
    item: NormalizedSourceItem,
    doc_key: str,
    source_key: str,
    import_run_key: str,
    now: str,
    counts: dict[str, int],
    *,
    method: str,
    evidence: Callable[[str], str],
    provenance: dict[str, Any],
) -> None:
    for tag in item.tags:
        key = topic_key(tag)
        counts["topics"] += int(
            repository.upsert(
                "topics",
                {
                    "_key": key,
                    "label": tag,
                    "language": "unknown",
                    "description": "",
                    "confidence": 1.0,
                    "metadata": {"source": method, "source_key": source_key},
                },
            )["created"],
        )
        counts["edges"] += int(
            repository.upsert_edge(
                "document_mentions_topic",
                {
                    "_key": stable_key(doc_key, key, prefix="edge"),
                    "_from": f"documents/{doc_key}",
                    "_to": f"topics/{key}",
                    "confidence": 1.0,
                    "method": method,
                    "evidence": evidence(tag),
                    "import_run_key": import_run_key,
                    "provenance": provenance,
                    "created_at": now,
                },
            )["created"],
        )


def upsert_author(
    repository: KnowledgeRepository,
    item: NormalizedSourceItem,
    doc_key: str,
    source_key: str,
    import_run_key: str,
    now: str,
    counts: dict[str, int],
    *,
    method: str,
    provenance: dict[str, Any],
) -> None:
    if not item.author:
        return
    author_key = slugify(item.author, fallback="author")
    counts["authors"] += int(
        repository.upsert(
            "authors",
            {
                "_key": author_key,
                "display_name": item.author,
                "aliases": [],
                "metadata": {"source": method, "source_key": source_key},
            },
        )["created"],
    )
    counts["edges"] += int(
        repository.upsert_edge(
            "document_mentions_author",
            {
                "_key": stable_key(doc_key, author_key, prefix="edge"),
                "_from": f"documents/{doc_key}",
                "_to": f"authors/{author_key}",
                "confidence": 1.0,
                "method": method,
                "evidence": item.author,
                "import_run_key": import_run_key,
                "provenance": provenance,
                "created_at": now,
            },
        )["created"],
    )


def upsert_chunks(
    repository: KnowledgeRepository,
    settings: Settings,
    item: NormalizedSourceItem,
    doc_key: str,
    raw: dict[str, Any],
    import_run_key: str,
    now: str,
    counts: dict[str, int],
    *,
    chunk_metadata: dict[str, Any],
    topic_method: str | None = None,
    topic_evidence: Callable[[str], str] = lambda tag: tag,
    provenance: dict[str, Any] | None = None,
) -> None:
    for chunk in split_text(item.text):
        c_key = chunk_key(doc_key, chunk.ordinal, chunk.text)
        counts["chunks"] += int(
            repository.upsert(
                "chunks",
                {
                    "_key": c_key,
                    "document_key": doc_key,
                    "ordinal": chunk.ordinal,
                    "text": chunk.text,
                    "token_count": chunk.token_count,
                    "char_start": chunk.char_start,
                    "char_end": chunk.char_end,
                    "embedding": hash_embedding(chunk.text, dimension=settings.embedding_dimension),
                    "embedding_model": HASH_EMBEDDING_MODEL,
                    "metadata": chunk_metadata,
                },
            )["created"],
        )
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
        if topic_method:
            for tag in item.tags:
                key = topic_key(tag)
                counts["edges"] += int(
                    repository.upsert_edge(
                        "document_mentions_topic",
                        {
                            "_key": stable_key(c_key, key, prefix="edge"),
                            "_from": f"chunks/{c_key}",
                            "_to": f"topics/{key}",
                            "confidence": 1.0,
                            "method": topic_method,
                            "evidence": topic_evidence(tag),
                            "import_run_key": import_run_key,
                            "provenance": provenance,
                            "created_at": now,
                        },
                    )["created"],
                )
