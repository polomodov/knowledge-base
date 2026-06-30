from __future__ import annotations

import hashlib
import math
import re


TOKEN_RE = re.compile(r"[a-zA-Zа-яА-Я0-9_-]+")
HASH_EMBEDDING_MODEL = "hash-v1"


def hash_embedding(text: str, *, dimension: int = 8) -> list[float]:
    """Deterministic local embedding for tests and local-only source slices.

    This is not a semantic model. It is intentionally small, private, and
    reproducible so the first pipeline can exercise vector plumbing without
    sending personal text to an external API.
    """

    vector = [0.0] * dimension
    tokens = TOKEN_RE.findall(text.lower())
    if not tokens:
        return vector

    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = digest[0] % dimension
        sign = 1.0 if digest[1] % 2 == 0 else -1.0
        weight = 1.0 + (digest[2] % 7) / 10.0
        vector[index] += sign * weight

    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [round(value / norm, 6) for value in vector]


def fixture_embedding(text: str, *, dimension: int = 8) -> list[float]:
    return hash_embedding(text, dimension=dimension)


def validate_vector(vector: list[float], *, dimension: int = 8) -> None:
    if len(vector) != dimension:
        raise ValueError(f"Expected vector dimension {dimension}, got {len(vector)}")
    if any(not isinstance(value, int | float) for value in vector):
        raise ValueError("Embedding vector must contain only numbers")
