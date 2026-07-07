import os
from pathlib import Path
from zipfile import ZipFile

import pytest

from knowledge_base.arango import ArangoClient
from knowledge_base.config import load_settings
from knowledge_base.indexing import rebuild_indexes
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.retrieval import graph_neighbors, hybrid_search, semantic_search, text_search
from knowledge_base.schema import bootstrap_schema
from knowledge_base.sources.medium_export import ingest_medium_export

pytestmark = pytest.mark.integration


ARCHIVE_DIR = Path("tests/fixtures/medium_export")


def _integration_enabled() -> bool:
    return os.getenv("KB_RUN_INTEGRATION") == "1"


@pytest.mark.skipif(not _integration_enabled(), reason="set KB_RUN_INTEGRATION=1 with ArangoDB running")
def test_medium_export_ingest_end_to_end(tmp_path: Path) -> None:
    settings = load_settings()
    client = ArangoClient(settings)
    repository = KnowledgeRepository(client)

    bootstrap_schema(client)
    ingest_result = ingest_medium_export(repository, settings, archive_path=ARCHIVE_DIR)
    dedupe_result = ingest_medium_export(repository, settings, archive_path=ARCHIVE_DIR)
    zip_path = _make_archive_zip(tmp_path)
    zip_result = ingest_medium_export(repository, settings, archive_path=zip_path)
    index_result = rebuild_indexes(repository, target="all")
    text = text_search(repository, "preserves provenance")
    source_text = text_search(repository, "preserves provenance", source_key="medium-export")
    invalid_source_text = text_search(repository, "preserves provenance", source_key="missing-source")
    graph = graph_neighbors(repository, author="alexander-polomodov")
    documents_graph = graph_neighbors(
        repository,
        author="alexander-polomodov",
        source_key="medium-export",
        documents_only=True,
        limit=20,
    )
    invalid_source_graph = graph_neighbors(
        repository,
        author="alexander-polomodov",
        source_key="missing-source",
        documents_only=True,
    )
    semantic = semantic_search(
        repository,
        "durable knowledge base phrase",
        dimension=settings.embedding_dimension,
        source_key="medium-export",
    )
    invalid_source_semantic = semantic_search(
        repository,
        "durable knowledge base phrase",
        dimension=settings.embedding_dimension,
        source_key="missing-source",
    )
    hybrid = hybrid_search(repository, "durable knowledge base phrase", dimension=settings.embedding_dimension)
    source_hybrid = hybrid_search(
        repository,
        "durable knowledge base phrase",
        dimension=settings.embedding_dimension,
        source_key="medium-export",
    )
    invalid_source_hybrid = hybrid_search(
        repository,
        "durable knowledge base phrase",
        dimension=settings.embedding_dimension,
        source_key="missing-source",
    )

    assert ingest_result["status"] == "ok"
    assert ingest_result["source_key"] == "medium-export"
    assert ingest_result["archive"]["kind"] == "directory"
    assert ingest_result["skipped"] == [{"guid": "fed456fed456", "reason": "draft_excluded"}]
    assert dedupe_result["created"]["documents"] == 0
    assert dedupe_result["created"]["chunks"] == 0
    assert zip_result["status"] == "ok"
    assert zip_result["archive"]["kind"] == "zip"
    assert index_result["status"] == "ok"

    document = _document(repository, "medium-post-abc123abc123")
    assert document["metadata"]["medium_post"]["post_id"] == "abc123abc123"
    assert document["metadata"]["medium_post"]["raw_snapshot_key"]
    assert document["metadata"]["images"][0]["data_image_id"] == "1*fixture.png"
    assert document["metadata"]["links"] == [
        "https://example.com/reference",
        "https://medium.com/@someone/linked-post-999999999999",
    ]

    raw = _raw_snapshot(repository, ingest_result["archive"]["manifest_sha256"])
    assert raw["storage_kind"] == "local_manifest"
    assert raw["metadata"]["archive"]["total_files"] == 4

    assert any(result["provenance"]["source_key"] == "medium-export" for result in text["results"])
    assert source_text["results"]
    assert {result["provenance"]["source_key"] for result in source_text["results"]} == {"medium-export"}
    assert invalid_source_text["status"] == "ok"
    assert invalid_source_text["results"] == []
    assert any(result["provenance"]["source_key"] == "medium-export" for result in graph["results"])
    assert documents_graph["results"]
    assert {result["kind"] for result in documents_graph["results"]} == {"document"}
    assert all(result["chunk_key"] is None for result in documents_graph["results"])
    assert {result["provenance"]["source_key"] for result in documents_graph["results"]} == {"medium-export"}
    assert len({result["document_key"] for result in documents_graph["results"]}) == len(documents_graph["results"])
    assert invalid_source_graph["status"] == "ok"
    assert invalid_source_graph["results"] == []
    assert semantic["status"] == "ok"
    assert semantic["results"]
    assert {result["provenance"]["source_key"] for result in semantic["results"]} == {"medium-export"}
    assert invalid_source_semantic["status"] == "ok"
    assert invalid_source_semantic["results"] == []
    assert hybrid["status"] in {"ok", "degraded"}
    assert any(result["provenance"]["source_key"] == "medium-export" for result in hybrid["results"])
    assert source_hybrid["status"] in {"ok", "degraded"}
    assert source_hybrid["results"]
    assert {result["provenance"]["source_key"] for result in source_hybrid["results"]} == {"medium-export"}
    assert invalid_source_hybrid["status"] == "ok"
    assert invalid_source_hybrid["results"] == []
    _assert_medium_provenance(text["results"])
    _assert_medium_provenance(graph["results"])
    _assert_medium_provenance(hybrid["results"])


def _make_archive_zip(tmp_path: Path) -> Path:
    zip_path = tmp_path / "medium-export.zip"
    with ZipFile(zip_path, "w") as archive:
        for path in ARCHIVE_DIR.rglob("*"):
            if path.is_file():
                archive.write(path, Path("Medium Export") / path.relative_to(ARCHIVE_DIR))
    return zip_path


def _document(repository: KnowledgeRepository, canonical_id: str) -> dict:
    rows = repository.client.aql(
        """
        FOR document IN documents
          FILTER document.source_key == "medium-export" AND document.canonical_id == @canonical_id
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
          FILTER raw.source_key == "medium-export" AND raw.sha256 == @sha256
          LIMIT 1
          RETURN raw
        """,
        {"sha256": sha256},
    )
    assert rows
    return rows[0]


def _assert_medium_provenance(results: list[dict]) -> None:
    for result in results:
        if result["provenance"]["source_key"] == "medium-export":
            assert result["provenance"]["raw_snapshot_key"]
            assert result["provenance"]["import_run_key"]
            assert result["provenance"]["medium_post"]["post_id"]
            assert result["provenance"]["medium_post"]["local_post_path"].startswith("posts/")
