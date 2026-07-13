"""Prove import_runs / index_runs leave status=error (not running) after mid-run crashes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from knowledge_base.config import Settings
from knowledge_base.fixture import ingest_fixture
from knowledge_base.indexing import rebuild_indexes
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.sources.tellmeabout_tech import ingest_tellmeabout_tech


class _RecordingRepository:
    def __init__(self, *, fail_on_collection: str | None = None) -> None:
        self.docs: dict[str, list[dict[str, Any]]] = {}
        self.fail_on_collection = fail_on_collection
        self.client = object()

    def upsert(self, collection: str, document: dict[str, Any]) -> dict[str, int]:
        if self.fail_on_collection and collection == self.fail_on_collection:
            # Allow the initial running import_run / index_run write, then fail later writes
            # to the same collection only after a running document already exists.
            existing = self.docs.get(collection, [])
            if any(doc.get("status") == "running" for doc in existing):
                raise RuntimeError(f"forced failure writing {collection}")
        self.docs.setdefault(collection, []).append(dict(document))
        return {"created": 1}

    def upsert_edge(self, collection: str, document: dict[str, Any]) -> dict[str, int]:
        return self.upsert(collection, document)

    def count(self, collection: str) -> int:
        return len(self.docs.get(collection, []))


def _as_repo(repository: _RecordingRepository) -> KnowledgeRepository:
    return cast(KnowledgeRepository, repository)


def _latest(repository: _RecordingRepository, collection: str) -> dict[str, Any]:
    docs = repository.docs[collection]
    assert docs, f"expected documents in {collection}"
    return docs[-1]


def test_rebuild_indexes_marks_error_when_work_fails_after_running() -> None:
    repository = _RecordingRepository()

    with (
        patch("knowledge_base.indexing.bootstrap_schema", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError, match="boom"),
    ):
        rebuild_indexes(_as_repo(repository), target="text")

    run = _latest(repository, "index_runs")
    assert run["status"] == "error"
    assert run["finished_at"] is not None
    assert run["error"] == "RuntimeError: boom"
    assert any(doc.get("status") == "running" for doc in repository.docs["index_runs"])


def test_ingest_fixture_marks_error_when_document_loop_fails() -> None:
    repository = _RecordingRepository()
    settings = Settings()

    with (
        patch("knowledge_base.fixture.bootstrap_schema", return_value={}),
        patch("knowledge_base.fixture._ingest_document", side_effect=RuntimeError("doc boom")),
        pytest.raises(RuntimeError, match="doc boom"),
    ):
        ingest_fixture(_as_repo(repository), settings)

    run = _latest(repository, "import_runs")
    assert run["status"] == "error"
    assert run["finished_at"] is not None
    assert run["error"] == "RuntimeError: doc boom"
    assert any(doc.get("status") == "running" for doc in repository.docs["import_runs"])


def test_ingest_tellmeabout_marks_error_when_item_loop_fails(tmp_path: Path) -> None:
    feed = tmp_path / "feed.xml"
    feed.write_text(
        """<?xml version="1.0"?>
        <rss version="2.0"><channel>
          <title>Tell Me About Tech</title>
          <item>
            <title>Systems note</title>
            <guid>https://tellmeabout.tech/systems</guid>
            <link>https://tellmeabout.tech/systems</link>
            <pubDate>Sat, 20 Jun 2026 09:15:00 +0000</pubDate>
            <description>A short note about systems.</description>
          </item>
        </channel></rss>
        """,
        encoding="utf-8",
    )
    repository = _RecordingRepository()
    settings = Settings()

    with (
        patch("knowledge_base.sources.tellmeabout_tech.bootstrap_schema", return_value={}),
        patch("knowledge_base.sources.tellmeabout_tech._ingest_item", side_effect=RuntimeError("item boom")),
        pytest.raises(RuntimeError, match="item boom"),
    ):
        ingest_tellmeabout_tech(_as_repo(repository), settings, input_path=feed)

    run = _latest(repository, "import_runs")
    assert run["status"] == "error"
    assert run["finished_at"] is not None
    assert run["error"] == "RuntimeError: item boom"
    assert any(doc.get("status") == "running" for doc in repository.docs["import_runs"])
