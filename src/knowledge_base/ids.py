from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import IO


def sha256_text(value: str | bytes) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def sha256_stream(handle: IO[bytes], *, chunk_size: int = 65536) -> str:
    # Hash a binary stream in fixed-size blocks so large media files are never fully loaded
    # into memory (findings #24, #26).
    digest = hashlib.sha256()
    for block in iter(lambda: handle.read(chunk_size), b""):
        digest.update(block)
    return digest.hexdigest()


def sha256_file(path: Path, *, chunk_size: int = 65536) -> str:
    with path.open("rb") as handle:
        return sha256_stream(handle, chunk_size=chunk_size)


def slugify(value: str, *, fallback: str = "item") -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-_")
    return slug or fallback


def topic_key(label: str) -> str:
    """Canonical, non-ASCII-safe topic key shared by every source adapter.

    ASCII labels collapse to a readable slug ("Product Thinking" -> "product-thinking").
    Non-ASCII (e.g. Cyrillic) or slug-empty labels get a stable, collision-free
    hash-suffixed key ("машинное обучение" -> "topic-<digest>") instead of every
    distinct label collapsing into a single "topic" bucket. A given label always
    yields the same key regardless of which adapter produced it.
    """
    normalized = label.lstrip("#").strip().lower()
    if not normalized:
        return "topic"
    slug = re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9_-]+", "-", normalized)).strip("-_")
    if slug and normalized.isascii():
        return slug
    return f"topic-{sha256_text(normalized)[:12]}"


def work_key(title: str) -> str:
    """Canonical work key shared by adapters that extract book/work titles.

    ASCII titles use a readable slug plus a hash suffix so lossy punctuation
    stripping (e.g. "C# in Depth" vs "C in Depth") cannot collide. Non-ASCII
    titles get a stable hash-suffixed key ("системное мышление" -> "work-<digest>").
    """
    normalized = title.strip().lower()
    if not normalized:
        return "work"
    slug = re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9_-]+", "-", normalized)).strip("-_")
    digest = sha256_text(normalized)[:12]
    if normalized.isascii():
        if slug:
            return f"{slug}-{digest}"
        return f"work-{digest}"
    return f"work-{digest}"


def stable_key(*parts: str, prefix: str | None = None, max_slug: int = 64) -> str:
    visible = slugify("-".join(part for part in parts if part), fallback=prefix or "item")[:max_slug]
    digest = sha256_text("|".join(parts))[:12]
    if prefix:
        return f"{prefix}-{visible}-{digest}"
    return f"{visible}-{digest}"


def document_key(source_key: str, canonical_id: str) -> str:
    return stable_key(source_key, canonical_id, prefix="doc")


def chunk_key(document_key_value: str, ordinal: int, text: str) -> str:
    return stable_key(document_key_value, str(ordinal), sha256_text(text)[:16], prefix="chunk")
