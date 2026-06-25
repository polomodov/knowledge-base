from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    text: str
    char_start: int
    char_end: int
    token_count: int


def split_text(text: str, *, max_chars: int = 360) -> list[Chunk]:
    normalized = " ".join(text.split())
    if not normalized:
        return []

    chunks: list[Chunk] = []
    start = 0
    buffer = ""
    buffer_start = 0
    sentences = re.split(r"(?<=[.!?])\s+", normalized)

    for sentence in sentences:
        if not sentence:
            continue
        candidate = f"{buffer} {sentence}".strip()
        if buffer and len(candidate) > max_chars:
            chunks.append(_make_chunk(len(chunks), buffer, buffer_start))
            buffer = sentence
            buffer_start = start
        else:
            if not buffer:
                buffer_start = start
            buffer = candidate
        start += len(sentence) + 1

    if buffer:
        chunks.append(_make_chunk(len(chunks), buffer, buffer_start))

    return chunks


def _make_chunk(ordinal: int, text: str, start: int) -> Chunk:
    return Chunk(
        ordinal=ordinal,
        text=text,
        char_start=start,
        char_end=start + len(text),
        token_count=len(text.split()),
    )
