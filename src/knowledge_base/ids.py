from __future__ import annotations

import hashlib
import re


def sha256_text(value: str | bytes) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def slugify(value: str, *, fallback: str = "item") -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-_")
    return slug or fallback


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
