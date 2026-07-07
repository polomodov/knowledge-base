import os

import pytest

from knowledge_base.arango import ArangoClient
from knowledge_base.config import load_settings
from knowledge_base.fixture import ingest_fixture
from knowledge_base.indexing import rebuild_indexes
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.retrieval import graph_neighbors, hybrid_search, semantic_search, text_search
from knowledge_base.schema import bootstrap_schema


pytestmark = pytest.mark.integration


def _integration_enabled() -> bool:
    return os.getenv("KB_RUN_INTEGRATION") == "1"


@pytest.mark.skipif(not _integration_enabled(), reason="set KB_RUN_INTEGRATION=1 with ArangoDB running")
def test_fixture_pipeline_end_to_end() -> None:
    settings = load_settings()
    client = ArangoClient(settings)
    repository = KnowledgeRepository(client)

    bootstrap_schema(client)
    ingest_result = ingest_fixture(repository, settings)
    created_before = _document_created_at(repository)
    dedupe_result = ingest_fixture(repository, settings)
    created_after = _document_created_at(repository)
    index_result = rebuild_indexes(repository, target="all")
    text = text_search(repository, "systems thinking")
    no_match = text_search(repository, "zzzxqvnomatch928371")
    semantic = semantic_search(repository, "ideas across books", dimension=settings.embedding_dimension)
    topic_graph = graph_neighbors(repository, topic="systems-thinking")
    author_graph = graph_neighbors(repository, author="fixture-author")
    work_graph = graph_neighbors(repository, work="fixture-work-knowledge-graphs")
    missing_graph = graph_neighbors(repository, topic="missing-topic")
    hybrid = hybrid_search(repository, "systems thinking writing workflow", dimension=settings.embedding_dimension)

    assert ingest_result["status"] == "ok"
    assert dedupe_result["created"]["documents"] == 0
    assert dedupe_result["created"]["chunks"] == 0
    # created_at is immutable across re-ingest (finding #11).
    assert created_before
    assert created_after == created_before
    # deduplicated is computed from real totals, not a hardcoded flag (finding #35):
    # created + deduplicated == total, and a second identical ingest deduplicates
    # everything. Kept independent of any pre-existing DB state.
    for key in ("documents", "chunks"):
        total = ingest_result["created"][key] + ingest_result["deduplicated"][key]
        assert total >= 1
        assert dedupe_result["created"][key] == 0
        assert dedupe_result["deduplicated"][key] == total
    assert index_result["status"] == "ok"
    assert text["results"]
    assert no_match["results"] == []
    # text and semantic each return at most one row per document (finding #14).
    assert _unique_document_keys(text["results"])
    _assert_provenance(text["results"])
    assert semantic["status"] in {"ok", "degraded"}
    assert _unique_document_keys(semantic["results"])
    _assert_provenance(semantic["results"])
    assert topic_graph["status"] == "ok"
    assert author_graph["status"] == "ok"
    assert work_graph["status"] == "ok"
    assert missing_graph["status"] == "ok"
    assert missing_graph["results"] == []
    assert {result["kind"] for result in topic_graph["results"]} & {"document", "chunk", "topic", "author", "work"}
    assert {result["kind"] for result in author_graph["results"]} & {"document", "topic", "work"}
    assert {result["kind"] for result in work_graph["results"]} & {"document", "topic", "author"}
    _assert_provenance(topic_graph["results"])
    assert hybrid["status"] in {"ok", "degraded"}
    assert hybrid["results"]
    assert {"bm25", "vector", "graph_boost"} <= set(hybrid["results"][0]["score_components"])
    # One row per document (finding #14) and no negative fused scores (finding #16).
    assert _unique_document_keys(hybrid["results"])
    assert all(result["score"] >= 0 for result in hybrid["results"])
    assert all(result["score_components"]["graph_boost"] is None for result in hybrid["results"])
    _assert_provenance(hybrid["results"])


def _unique_document_keys(results: list[dict]) -> bool:
    keys = [result["document_key"] for result in results]
    return len(keys) == len(set(keys))


def _document_created_at(repository: KnowledgeRepository) -> dict[str, str]:
    rows = repository.client.aql(
        "FOR d IN documents SORT d._key RETURN {key: d._key, created_at: d.created_at}",
    )
    return {row["key"]: row["created_at"] for row in rows}


def _assert_provenance(results: list[dict]) -> None:
    for result in results:
        provenance = result["provenance"]
        assert provenance["source_key"]
        assert provenance["raw_snapshot_key"]
        assert provenance["import_run_key"]
