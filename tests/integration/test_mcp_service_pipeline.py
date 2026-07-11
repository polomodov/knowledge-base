import asyncio
import contextlib
import dataclasses
import json
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from knowledge_base.arango import ArangoClient, ArangoError
from knowledge_base.config import Settings, load_settings
from knowledge_base.constants import DOCUMENT_COLLECTIONS, EDGE_COLLECTIONS, RELATED_EDGE_METHOD
from knowledge_base.fixture import ingest_fixture
from knowledge_base.indexing import rebuild_indexes
from knowledge_base.mcp_service import KnowledgeBaseMCPService
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.schema import bootstrap_schema
from knowledge_base.sources.medium_export import ingest_medium_export

pytestmark = pytest.mark.integration


MEDIUM_ARCHIVE_DIR = Path("tests/fixtures/medium_export")


def _integration_enabled() -> bool:
    return os.getenv("KB_RUN_INTEGRATION") == "1"


@pytest.fixture
def mcp_pipeline() -> Iterator[tuple[Settings, KnowledgeRepository]]:
    base = load_settings()
    settings = dataclasses.replace(
        base,
        arango_database=f"{base.arango_database}_mcp_service",
        embedding_provider="hash",
        retrieval_min_similarity=0.99,
    )
    client = ArangoClient(settings)
    with contextlib.suppress(ArangoError):
        client.request("DELETE", f"/_api/database/{settings.arango_database}", expected=(200, 404))

    repository = KnowledgeRepository(client)
    try:
        bootstrap_schema(client, embedding_dimension=settings.embedding_dimension)
        ingest_fixture(repository, settings)
        ingest_medium_export(repository, settings, archive_path=MEDIUM_ARCHIVE_DIR)
        rebuild_indexes(
            repository,
            target="all",
            embedding_dimension=settings.embedding_dimension,
            settings=settings,
        )
        _connect_medium_documents(repository)
        rebuild_indexes(
            repository,
            target="communities",
            embedding_dimension=settings.embedding_dimension,
            settings=settings,
        )
        yield settings, repository
    finally:
        with contextlib.suppress(ArangoError):
            client.request("DELETE", f"/_api/database/{settings.arango_database}", expected=(200, 404))


@pytest.mark.skipif(not _integration_enabled(), reason="set KB_RUN_INTEGRATION=1 with ArangoDB running")
def test_mcp_service_read_only_retrieval_end_to_end(
    mcp_pipeline: tuple[Settings, KnowledgeRepository],
) -> None:
    settings, repository = mcp_pipeline
    service = KnowledgeBaseMCPService(repository=repository, settings=settings)
    document_key = _document_key(repository, "medium-post-def456def456")
    query = _chunk_text(repository, document_key)
    counts_before = _collection_counts(repository)

    text = _wait_for_results(service, query, mode="text")
    semantic = service.search(query, mode="semantic", source_key="medium-export", limit=50)
    hybrid = service.search(query, mode="hybrid", source_key="medium-export", limit=50)
    local = service.search(query, mode="local", source_key="medium-export", limit=1)
    global_result = service.search(
        query,
        mode="global",
        source_key="medium-export",
        limit=50,
        community_limit=50,
    )
    invalid_source = service.search(
        query,
        mode="text",
        source_key="missing-source",
    )
    document = service.get_document(document_key, max_chars=2_000)
    graph = service.graph_neighbors(
        start_type="author",
        key="alexander-polomodov",
        source_key="medium-export",
        documents_only=True,
        limit=20,
    )
    sources = service.list_sources()
    health = service.health()
    counts_after = _collection_counts(repository)
    stdio_search, stdio_document = _stdio_read(settings, query, document_key)

    for search in (text, semantic, hybrid):
        assert search["status"] in {"ok", "degraded"}
        assert search["results"]
        assert len(search["results"]) <= 20
        assert {result["provenance"]["source_key"] for result in search["results"]} == {"medium-export"}
        assert all(result["resource_uri"].startswith("kb://documents/") for result in search["results"])

    assert local["status"] in {"ok", "degraded"}
    assert local["mode"] == "graphrag-local"
    assert local["seeds"]
    assert len(local["seeds"]) == 1
    assert local["seeds"][0]["resource_uri"].startswith("kb://documents/")
    assert local["communities"]

    assert global_result["status"] in {"ok", "degraded"}
    assert global_result["mode"] == "graphrag-global"
    assert global_result["communities"]
    assert len(global_result["communities"]) <= 20
    global_documents = [result for community in global_result["communities"] for result in community["documents"]]
    assert global_documents
    assert {result["provenance"]["source_key"] for result in global_documents} == {"medium-export"}
    assert all(result["resource_uri"].startswith("kb://documents/") for result in global_documents)

    assert invalid_source["status"] == "ok"
    assert invalid_source["results"] == []

    assert document["status"] == "ok"
    assert document["document_key"] == document_key
    assert document["resource_uri"] == f"kb://documents/{document_key}"
    assert document["provenance"]["source_key"] == "medium-export"
    assert document["provenance"]["raw_snapshot_key"]
    assert document["provenance"]["import_run_key"]
    assert document["provenance"]["medium_post"]["post_id"] == "def456def456"

    assert graph["status"] == "ok"
    assert graph["documents_only"] is True
    assert graph["results"]
    assert {result["kind"] for result in graph["results"]} == {"document"}
    assert {result["provenance"]["source_key"] for result in graph["results"]} == {"medium-export"}
    assert len({result["document_key"] for result in graph["results"]}) == len(graph["results"])

    assert sources["status"] == "ok"
    assert any(source["source_key"] == "medium-export" for source in sources["sources"])
    assert health["status"] in {"ok", "degraded"}
    assert health["database"] == settings.arango_database

    exposed_payloads = [
        text,
        semantic,
        hybrid,
        local,
        global_result,
        invalid_source,
        document,
        graph,
        sources,
        health,
    ]
    assert '"payload"' not in json.dumps(exposed_payloads, ensure_ascii=False)
    assert counts_after == counts_before
    assert stdio_search["status"] == "ok"
    assert stdio_search["mode"] == "text"
    assert stdio_search["results"]
    assert f"document_key: `{document_key}`" in stdio_document
    assert "payload" not in stdio_document


def _stdio_read(settings: Settings, query: str, document_key: str) -> tuple[dict[str, object], str]:
    return asyncio.run(_stdio_read_async(settings, query, document_key))


async def _stdio_read_async(settings: Settings, query: str, document_key: str) -> tuple[dict[str, object], str]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from pydantic import AnyUrl

    env = dict(os.environ)
    env.update(
        {
            "KB_ARANGO_URL": settings.arango_url,
            "KB_ARANGO_DATABASE": settings.arango_database,
            "KB_ARANGO_USER": settings.arango_user,
            "KB_ARANGO_PASSWORD": settings.arango_password,
            "KB_EMBEDDING_PROVIDER": settings.embedding_provider,
            "KB_EMBEDDING_DIMENSION": str(settings.embedding_dimension),
            "KB_RETRIEVAL_MIN_SIMILARITY": str(settings.retrieval_min_similarity),
        },
    )
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "knowledge_base.mcp_server"],
        cwd=Path.cwd(),
        env=env,
    )
    async with (
        stdio_client(server) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        tool_result = await session.call_tool(
            "kb_search",
            {"query": query, "mode": "text", "source_key": "medium-export", "limit": 2},
        )
        resource_result = await session.read_resource(AnyUrl(f"kb://documents/{document_key}"))

    assert tool_result.isError is False
    payload = tool_result.structuredContent
    assert isinstance(payload, dict)
    if set(payload) == {"result"} and isinstance(payload["result"], dict):
        payload = payload["result"]
    resource_text = next(content.text for content in resource_result.contents if hasattr(content, "text"))
    return payload, resource_text


def _document_key(repository: KnowledgeRepository, canonical_id: str) -> str:
    rows = repository.client.aql(
        """
        FOR document IN documents
          FILTER document.source_key == "medium-export" AND document.canonical_id == @canonical_id
          LIMIT 1
          RETURN document._key
        """,
        {"canonical_id": canonical_id},
    )
    assert rows
    return rows[0]


def _chunk_key(repository: KnowledgeRepository, document_key: str) -> str:
    rows = repository.client.aql(
        """
        FOR chunk IN chunks
          FILTER chunk.document_key == @document_key
          SORT chunk.ordinal ASC
          LIMIT 1
          RETURN chunk._key
        """,
        {"document_key": document_key},
    )
    assert rows
    return rows[0]


def _chunk_text(repository: KnowledgeRepository, document_key: str) -> str:
    rows = repository.client.aql(
        """
        FOR chunk IN chunks
          FILTER chunk.document_key == @document_key
          SORT chunk.ordinal ASC
          LIMIT 1
          RETURN chunk.text
        """,
        {"document_key": document_key},
    )
    assert rows
    return rows[0]


def _connect_medium_documents(repository: KnowledgeRepository) -> None:
    first = _document_key(repository, "medium-post-abc123abc123")
    second = _document_key(repository, "medium-post-def456def456")
    repository.upsert_edge(
        "item_related_to_item",
        {
            "_key": "mcp-service-medium-relation",
            "_from": f"chunks/{_chunk_key(repository, first)}",
            "_to": f"chunks/{_chunk_key(repository, second)}",
            "weight": 0.95,
            "method": RELATED_EDGE_METHOD,
        },
    )


def _wait_for_results(
    service: KnowledgeBaseMCPService,
    query: str,
    *,
    mode: str,
) -> dict[str, object]:
    result: dict[str, object] = {}
    for _ in range(20):
        result = service.search(query, mode=mode, source_key="medium-export", limit=50)
        if result.get("results"):
            return result
        time.sleep(0.25)
    return result


def _collection_counts(repository: KnowledgeRepository) -> dict[str, int]:
    return {collection: repository.count(collection) for collection in [*DOCUMENT_COLLECTIONS, *EDGE_COLLECTIONS]}
