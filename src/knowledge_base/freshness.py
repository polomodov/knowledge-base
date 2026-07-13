"""Derived-index freshness checks shared by research and visualization."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

RELATED_MISSING_AFTER_EMBEDDINGS = "related_index_missing_after_embeddings"
RELATED_OLDER_THAN_EMBEDDINGS = "related_older_than_embeddings"
COMMUNITIES_MISSING_AFTER_RELATED = "communities_index_missing_after_related"
COMMUNITIES_OLDER_THAN_RELATED = "communities_older_than_related"
COMMUNITIES_STALE_AFTER_EMBEDDINGS = "communities_stale_after_embeddings_rebuild"

_RELATED_MISSING_MESSAGE = (
    "Embeddings exist but related similarity edges were not rebuilt; run `kb index rebuild --target related`."
)
_RELATED_OLDER_MESSAGE = "Related similarity graph is older than embeddings; run `kb index rebuild --target related`."
_COMMUNITIES_MISSING_MESSAGE = (
    "Related similarity graph exists but communities were not rebuilt; run `kb index rebuild --target communities`."
)
_COMMUNITIES_OLDER_MESSAGE = "Communities are older than the similarity graph; run `kb index rebuild --target communities`."
_COMMUNITIES_STALE_AFTER_EMBEDDINGS_MESSAGE = (
    "Communities still reflect a similarity graph that is missing or older than embeddings; "
    "run `kb index rebuild --target related` and `kb index rebuild --target communities`."
)


def _finished_at(run: Mapping[str, Any] | None) -> str | None:
    if not isinstance(run, Mapping):
        return None
    value = run.get("finished_at")
    return value if isinstance(value, str) and value else None


def derived_index_stale_codes(index_runs: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Return stable warning codes when derived indexes lag embeddings/related."""
    if not isinstance(index_runs, Mapping):
        return ()
    codes: list[str] = []
    embeddings = index_runs.get("embeddings")
    related = index_runs.get("related")
    communities = index_runs.get("communities")
    emb_finished = _finished_at(embeddings if isinstance(embeddings, Mapping) else None)
    related_finished = _finished_at(related if isinstance(related, Mapping) else None)
    communities_finished = _finished_at(communities if isinstance(communities, Mapping) else None)

    related_invalid_vs_embeddings = False
    if emb_finished and not related_finished:
        codes.append(RELATED_MISSING_AFTER_EMBEDDINGS)
        related_invalid_vs_embeddings = True
    elif emb_finished and related_finished and related_finished < emb_finished:
        codes.append(RELATED_OLDER_THAN_EMBEDDINGS)
        related_invalid_vs_embeddings = True

    if related_invalid_vs_embeddings and communities_finished:
        codes.append(COMMUNITIES_STALE_AFTER_EMBEDDINGS)
    elif related_finished and not communities_finished:
        codes.append(COMMUNITIES_MISSING_AFTER_RELATED)
    elif related_finished and communities_finished and communities_finished < related_finished:
        codes.append(COMMUNITIES_OLDER_THAN_RELATED)

    return tuple(codes)


def derived_index_stale_messages(index_runs: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """Structured warnings for viz meta payloads."""
    mapping = {
        RELATED_MISSING_AFTER_EMBEDDINGS: _RELATED_MISSING_MESSAGE,
        RELATED_OLDER_THAN_EMBEDDINGS: _RELATED_OLDER_MESSAGE,
        COMMUNITIES_MISSING_AFTER_RELATED: _COMMUNITIES_MISSING_MESSAGE,
        COMMUNITIES_OLDER_THAN_RELATED: _COMMUNITIES_OLDER_MESSAGE,
        COMMUNITIES_STALE_AFTER_EMBEDDINGS: _COMMUNITIES_STALE_AFTER_EMBEDDINGS_MESSAGE,
    }
    return [{"code": code, "message": mapping[code]} for code in derived_index_stale_codes(index_runs)]
