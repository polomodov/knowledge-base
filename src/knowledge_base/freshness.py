"""Derived-index freshness checks shared by research and visualization."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

RELATED_MISSING_AFTER_EMBEDDINGS = "related_index_missing_after_embeddings"
RELATED_OLDER_THAN_EMBEDDINGS = "related_older_than_embeddings"
RELATED_STALE_AFTER_IMPORT = "related_index_stale_after_import"
COMMUNITIES_MISSING_AFTER_RELATED = "communities_index_missing_after_related"
COMMUNITIES_OLDER_THAN_RELATED = "communities_older_than_related"
COMMUNITIES_STALE_AFTER_EMBEDDINGS = "communities_stale_after_embeddings_rebuild"
COMMUNITIES_STALE_AFTER_IMPORT = "communities_index_stale_after_import"

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
_RELATED_STALE_AFTER_IMPORT_MESSAGE = (
    "New material was imported after the similarity graph was built, so it has no similarity edges; "
    "run `kb index rebuild --target related` and `kb index rebuild --target communities`."
)
_COMMUNITIES_STALE_AFTER_IMPORT_MESSAGE = (
    "New material was imported after communities were built, so it belongs to no community; "
    "run `kb index rebuild --target communities`."
)


def _finished_at(run: Mapping[str, Any] | None) -> str | None:
    if not isinstance(run, Mapping):
        return None
    value = run.get("finished_at")
    return value if isinstance(value, str) and value else None


def _parse_ts(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp (accepting a trailing Z) to an aware datetime for comparison.

    Comparing parsed datetimes rather than raw strings keeps ordering correct when timestamps mix
    whole-second and microsecond precision (index runs carry microseconds; import runs may not).
    """
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _finished(run: Any) -> datetime | None:
    return _parse_ts(_finished_at(run if isinstance(run, Mapping) else None))


def derived_index_stale_codes(
    index_runs: Mapping[str, Any] | None,
    *,
    import_finished_at: str | None = None,
) -> tuple[str, ...]:
    """Return stable warning codes when derived indexes lag embeddings, related, or a later import.

    ``import_finished_at`` anchors the chain to the corpus itself: an incremental ingest writes chunk
    embeddings inline but does not rebuild the similarity graph or communities, so those derived
    indexes silently stop covering the newest material unless we compare them to the latest import.
    """
    if not isinstance(index_runs, Mapping):
        return ()
    codes: list[str] = []
    emb_finished = _finished(index_runs.get("embeddings"))
    related_finished = _finished(index_runs.get("related"))
    communities_finished = _finished(index_runs.get("communities"))
    import_finished = _parse_ts(import_finished_at)

    # Track *why* related is stale: only an embeddings-driven staleness cascades to the
    # "communities reflect a stale similarity graph" signal; import-driven staleness has its own code.
    related_stale_vs_embeddings = False
    if emb_finished and not related_finished:
        codes.append(RELATED_MISSING_AFTER_EMBEDDINGS)
        related_stale_vs_embeddings = True
    elif emb_finished and related_finished and related_finished < emb_finished:
        codes.append(RELATED_OLDER_THAN_EMBEDDINGS)
        related_stale_vs_embeddings = True
    elif import_finished and related_finished and related_finished < import_finished:
        codes.append(RELATED_STALE_AFTER_IMPORT)

    if related_stale_vs_embeddings and communities_finished:
        codes.append(COMMUNITIES_STALE_AFTER_EMBEDDINGS)
    elif related_finished and not communities_finished:
        codes.append(COMMUNITIES_MISSING_AFTER_RELATED)
    elif related_finished and communities_finished and communities_finished < related_finished:
        codes.append(COMMUNITIES_OLDER_THAN_RELATED)
    elif import_finished and communities_finished and communities_finished < import_finished:
        codes.append(COMMUNITIES_STALE_AFTER_IMPORT)

    return tuple(codes)


def derived_index_stale_messages(
    index_runs: Mapping[str, Any] | None,
    *,
    import_finished_at: str | None = None,
) -> list[dict[str, str]]:
    """Structured warnings for viz meta payloads."""
    mapping = {
        RELATED_MISSING_AFTER_EMBEDDINGS: _RELATED_MISSING_MESSAGE,
        RELATED_OLDER_THAN_EMBEDDINGS: _RELATED_OLDER_MESSAGE,
        RELATED_STALE_AFTER_IMPORT: _RELATED_STALE_AFTER_IMPORT_MESSAGE,
        COMMUNITIES_MISSING_AFTER_RELATED: _COMMUNITIES_MISSING_MESSAGE,
        COMMUNITIES_OLDER_THAN_RELATED: _COMMUNITIES_OLDER_MESSAGE,
        COMMUNITIES_STALE_AFTER_EMBEDDINGS: _COMMUNITIES_STALE_AFTER_EMBEDDINGS_MESSAGE,
        COMMUNITIES_STALE_AFTER_IMPORT: _COMMUNITIES_STALE_AFTER_IMPORT_MESSAGE,
    }
    return [
        {"code": code, "message": mapping[code]}
        for code in derived_index_stale_codes(index_runs, import_finished_at=import_finished_at)
    ]
