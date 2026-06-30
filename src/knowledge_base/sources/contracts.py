from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NormalizedSourceItem:
    canonical_id: str
    title: str
    text: str
    url: str | None
    guid: str | None
    published_at: str | None
    language: str
    author: str | None
    tags: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedSourceFeed:
    title: str | None
    feed_url: str | None
    media_type: str
    items: list[NormalizedSourceItem]
    skipped: list[dict[str, str]]
