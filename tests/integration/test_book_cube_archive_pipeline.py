import os
from pathlib import Path
from zipfile import ZipFile

import pytest

from knowledge_base.arango import ArangoClient
from knowledge_base.config import load_settings
from knowledge_base.indexing import rebuild_indexes
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.retrieval import graph_neighbors, hybrid_search, text_search
from knowledge_base.schema import bootstrap_schema
from knowledge_base.sources.book_cube import ingest_book_cube_archive

pytestmark = pytest.mark.integration


ARCHIVE_DIR = Path("tests/fixtures/book_cube_owner_export")


def _integration_enabled() -> bool:
    return os.getenv("KB_RUN_INTEGRATION") == "1"


@pytest.mark.skipif(not _integration_enabled(), reason="set KB_RUN_INTEGRATION=1 with ArangoDB running")
def test_book_cube_owner_archive_ingest_end_to_end(tmp_path: Path) -> None:
    settings = load_settings()
    client = ArangoClient(settings)
    repository = KnowledgeRepository(client)

    bootstrap_schema(client)
    directory_result = ingest_book_cube_archive(repository, settings, archive_path=ARCHIVE_DIR)
    dedupe_result = ingest_book_cube_archive(repository, settings, archive_path=ARCHIVE_DIR)
    zip_path = _make_archive_zip(tmp_path)
    zip_result = ingest_book_cube_archive(repository, settings, archive_path=zip_path)
    index_result = rebuild_indexes(repository, target="all")
    text = text_search(repository, "владельческий экспорт")
    graph = graph_neighbors(repository, topic="archive")
    hybrid = hybrid_search(repository, "книжной подборкой архив", dimension=settings.embedding_dimension)

    assert directory_result["status"] == "ok"
    assert directory_result["source_key"] == "book-cube"
    assert directory_result["archive"]["kind"] == "directory"
    assert directory_result["skipped"] == [
        {"guid": "303", "reason": "empty_text"},
        {"guid": "304", "reason": "unsupported_type"},
    ]
    assert dedupe_result["created"]["documents"] == 0
    assert dedupe_result["created"]["chunks"] == 0
    assert zip_result["status"] == "ok"
    assert zip_result["archive"]["kind"] == "zip"
    assert index_result["status"] == "ok"

    document = _document(repository, "book_cube-301")
    attachments = document["metadata"]["attachments"]
    assert attachments[0]["relative_path"] == "photos/photo_301.jpg"
    assert attachments[0]["size_bytes"] > 0
    assert "payload" not in attachments[0]
    assert document["metadata"]["archive"]["manifest_sha256"]

    raw = _raw_snapshot(repository, directory_result["archive"]["result_sha256"])
    assert raw["storage_kind"] == "local_file"
    assert raw["metadata"]["archive"]["result_json"].endswith("result.json")

    assert any(result["provenance"]["source_key"] == "book-cube" for result in text["results"])
    assert any(result["provenance"]["source_key"] == "book-cube" for result in graph["results"])
    assert hybrid["status"] in {"ok", "degraded"}
    assert any(result["provenance"]["source_key"] == "book-cube" for result in hybrid["results"])


def _make_archive_zip(tmp_path: Path) -> Path:
    zip_path = tmp_path / "book-cube-export.zip"
    with ZipFile(zip_path, "w") as archive:
        for path in ARCHIVE_DIR.rglob("*"):
            if path.is_file():
                archive.write(path, Path("Book Cube Export") / path.relative_to(ARCHIVE_DIR))
    return zip_path


def _document(repository: KnowledgeRepository, canonical_id: str) -> dict:
    rows = repository.client.aql(
        """
        FOR document IN documents
          FILTER document.source_key == "book-cube" AND document.canonical_id == @canonical_id
          LIMIT 1
          RETURN document
        """,
        {"canonical_id": canonical_id},
    )
    assert rows
    return rows[0]


def _raw_snapshot(repository: KnowledgeRepository, sha256: str) -> dict:
    rows = repository.client.aql(
        """
        FOR raw IN raw_snapshots
          FILTER raw.source_key == "book-cube" AND raw.sha256 == @sha256
          LIMIT 1
          RETURN raw
        """,
        {"sha256": sha256},
    )
    assert rows
    return rows[0]
