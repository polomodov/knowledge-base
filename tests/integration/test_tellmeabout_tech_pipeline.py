import os
from pathlib import Path

import pytest

from knowledge_base.arango import ArangoClient
from knowledge_base.config import load_settings
from knowledge_base.indexing import rebuild_indexes
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.retrieval import graph_neighbors, hybrid_search, text_search
from knowledge_base.schema import bootstrap_schema
from knowledge_base.sources.tellmeabout_tech import ingest_tellmeabout_tech

pytestmark = pytest.mark.integration


FIXTURE = Path("tests/fixtures/tellmeabout_tech_feed.xml")


def _integration_enabled() -> bool:
    return os.getenv("KB_RUN_INTEGRATION") == "1"


@pytest.mark.skipif(not _integration_enabled(), reason="set KB_RUN_INTEGRATION=1 with ArangoDB running")
def test_tellmeabout_tech_feed_ingest_end_to_end() -> None:
    settings = load_settings()
    client = ArangoClient(settings)
    repository = KnowledgeRepository(client)

    bootstrap_schema(client)
    ingest_result = ingest_tellmeabout_tech(repository, settings, input_path=FIXTURE)
    dedupe_result = ingest_tellmeabout_tech(repository, settings, input_path=FIXTURE)
    index_result = rebuild_indexes(repository, target="all")
    text = text_search(repository, "durable knowledge base")
    graph = graph_neighbors(repository, topic="product-thinking")
    hybrid = hybrid_search(repository, "reliable AI tools provenance", dimension=settings.embedding_dimension)

    assert ingest_result["status"] == "ok"
    assert ingest_result["source_key"] == "tellmeabout-tech"
    assert ingest_result["input"]["kind"] == "file"
    assert ingest_result["skipped"] == [{"guid": "empty-draft", "reason": "empty_text"}]
    assert dedupe_result["created"]["documents"] == 0
    assert dedupe_result["created"]["chunks"] == 0
    assert index_result["status"] == "ok"
    assert text["results"]
    assert graph["status"] == "ok"
    assert graph["results"]
    assert hybrid["status"] in {"ok", "degraded"}
    assert hybrid["results"]
    _assert_tellmeabout_provenance(text["results"])
    _assert_tellmeabout_provenance(graph["results"])
    _assert_tellmeabout_provenance(hybrid["results"])


def _assert_tellmeabout_provenance(results: list[dict]) -> None:
    assert any(result["provenance"]["source_key"] == "tellmeabout-tech" for result in results)
    for result in results:
        if result["provenance"]["source_key"] == "tellmeabout-tech":
            assert result["provenance"]["raw_snapshot_key"]
            assert result["provenance"]["import_run_key"]
