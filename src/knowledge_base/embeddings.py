from __future__ import annotations

import hashlib
import math
import re
from functools import cache
from typing import TYPE_CHECKING, Any, Protocol

from knowledge_base.constants import VECTOR_DIMENSION

if TYPE_CHECKING:
    from knowledge_base.config import Settings

TOKEN_RE = re.compile(r"[a-zA-Zа-яА-Я0-9_-]+")
HASH_EMBEDDING_MODEL = "hash-v1"


class EmbeddingProviderError(RuntimeError):
    """Raised when an embedding provider cannot be built or is inconsistent with the index."""


class EmbeddingProvider(Protocol):
    """Turns text into a fixed-dimension vector; must be deterministic for a given text."""

    @property
    def model(self) -> str:
        """Stable identifier stored on each chunk as `embedding_model`."""

    @property
    def dimension(self) -> int:
        """Vector length; must match the ArangoDB vector index dimension."""

    def embed(self, text: str) -> list[float]:
        """Return the embedding vector for `text`."""


def hash_embedding(text: str, *, dimension: int = VECTOR_DIMENSION) -> list[float]:
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


def fixture_embedding(text: str, *, dimension: int = VECTOR_DIMENSION) -> list[float]:
    return hash_embedding(text, dimension=dimension)


def validate_vector(vector: list[float], *, dimension: int = VECTOR_DIMENSION) -> None:
    if len(vector) != dimension:
        raise ValueError(f"Expected vector dimension {dimension}, got {len(vector)}")
    if any(not isinstance(value, int | float) for value in vector):
        raise ValueError("Embedding vector must contain only numbers")


class HashEmbeddingProvider:
    """Default provider: deterministic, offline, zero-dependency (wraps `hash_embedding`)."""

    def __init__(self, *, dimension: int = VECTOR_DIMENSION) -> None:
        self._dimension = dimension

    @property
    def model(self) -> str:
        return HASH_EMBEDDING_MODEL

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, text: str) -> list[float]:
        return hash_embedding(text, dimension=self._dimension)


class LocalModelEmbeddingProvider:
    """Real semantic embeddings from a local sentence-transformers model (optional extra).

    The model is loaded once at construction. Its native dimension must match the configured
    `embedding.dimension` (which drives the vector index), so a mismatch fails fast with a
    message that tells the user which dimension to set.
    """

    def __init__(self, model_name: str, *, dimension: int) -> None:
        self._model_name = model_name
        self._model = _load_sentence_transformer(model_name)
        native = int(self._model.get_sentence_embedding_dimension())
        if native != dimension:
            raise EmbeddingProviderError(
                f"embedding.model {model_name!r} produces {native}-dim vectors but embedding.dimension "
                f"is {dimension}; set embedding.dimension = {native} and re-bootstrap the vector index."
            )
        self._dimension = dimension

    @property
    def model(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, text: str) -> list[float]:
        vector = self._model.encode(text, normalize_embeddings=True)
        return [round(float(value), 6) for value in vector.tolist()]


def _load_sentence_transformer(model_name: str) -> Any:
    try:
        from sentence_transformers import SentenceTransformer
    except ModuleNotFoundError as error:
        raise EmbeddingProviderError(
            "The 'local' embedding provider requires 'sentence-transformers', which is not installed. "
            "Install it with `uv pip install sentence-transformers` (deliberately kept out of the "
            "project's locked dependencies to preserve the zero-runtime-dependency default), or set "
            "embedding.provider = 'hash'."
        ) from error
    return SentenceTransformer(model_name)


@cache
def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Resolve the configured embedding provider (cached so a local model loads at most once).

    Both ingest (chunk embeddings) and retrieval (query embedding) go through this, so the
    stored vectors and the query vector always live in the same space.
    """
    provider = settings.embedding_provider
    if provider == "hash":
        return HashEmbeddingProvider(dimension=settings.embedding_dimension)
    if provider == "local":
        if not settings.embedding_model:
            raise EmbeddingProviderError("embedding.provider = 'local' requires embedding.model to be set.")
        return LocalModelEmbeddingProvider(settings.embedding_model, dimension=settings.embedding_dimension)
    raise EmbeddingProviderError(f"Unknown embedding provider {provider!r}; expected 'hash' or 'local'.")
