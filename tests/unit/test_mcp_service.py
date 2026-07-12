from __future__ import annotations

import asyncio
import inspect
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from knowledge_base import mcp_service
from knowledge_base.config import Settings
from knowledge_base.embeddings import EmbeddingProviderError
from knowledge_base.mcp_service import (
    KnowledgeBaseMCPService,
    clamp_int,
    document_key_from_uri,
    document_resource_uri,
    document_to_markdown,
    research_prompt,
)
from knowledge_base.repository import KnowledgeRepository


class _FakeRepository:
    client = object()


def _service() -> KnowledgeBaseMCPService:
    return KnowledgeBaseMCPService(
        repository=cast(KnowledgeRepository, _FakeRepository()),
        settings=Settings(
            embedding_dimension=8,
            embedding_provider="hash",
            retrieval_min_similarity=0.42,
        ),
    )


def test_clamp_int_bounds_values() -> None:
    assert clamp_int(None, minimum=1, maximum=20, default=5) == 5
    assert clamp_int("not-an-int", minimum=1, maximum=20, default=5) == 5
    assert clamp_int(0, minimum=1, maximum=20, default=5) == 1
    assert clamp_int(21, minimum=1, maximum=20, default=5) == 20


def test_search_normalizes_arguments_and_formats_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_hybrid_search(
        repository: object,
        query: str,
        *,
        limit: int,
        source_key: str | None,
        provider: object,
        min_similarity: float,
    ) -> dict[str, Any]:
        captured.update(
            {
                "repository": repository,
                "query": query,
                "limit": limit,
                "source_key": source_key,
                "provider": provider,
                "min_similarity": min_similarity,
            },
        )
        return {
            "status": "ok",
            "mode": "hybrid",
            "query": query,
            "degraded_components": [],
            "results": [
                {
                    "id": "chunks/chunk-1",
                    "title": "A result",
                    "snippet": "Agent-ready snippet",
                    "score": 0.9,
                    "score_components": {"bm25": 1.2, "vector": 0.8, "graph_boost": 0.1},
                    "document_key": "doc-1",
                    "chunk_key": "chunk-1",
                    "provenance": {
                        "source_key": "medium-export",
                        "raw_snapshot_key": "raw-1",
                        "import_run_key": "import-1",
                        "medium_post": {
                            "post_id": "abc123abc123",
                            "local_post_path": "posts/private.html",
                            "archive": {"root": "/private/raw/medium"},
                        },
                        "url": "https://example.com/post",
                    },
                },
            ],
        }

    monkeypatch.setattr(mcp_service, "hybrid_search", fake_hybrid_search)

    service = _service()
    result = service.search("query", mode="HYBRID", source_key="medium-export", limit=99)

    assert captured["repository"] is service.repository
    assert captured["query"] == "query"
    assert captured["limit"] == 20
    assert captured["source_key"] == "medium-export"
    assert captured["provider"].dimension == 8
    assert captured["provider"].model == "hash-v1"
    assert captured["min_similarity"] == pytest.approx(0.42)
    assert result["status"] == "ok"
    assert result["results"][0]["resource_uri"] == "kb://documents/doc-1"
    assert result["results"][0]["provenance"]["medium_post"]["post_id"] == "abc123abc123"
    assert "local_post_path" not in result["results"][0]["provenance"]["medium_post"]
    assert "archive" not in result["results"][0]["provenance"]["medium_post"]
    assert result["results"][0]["url"] == "https://example.com/post"


def test_text_search_does_not_build_an_embedding_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fail_if_called(settings: object) -> object:
        pytest.fail(f"text search must not build an embedding provider: {settings!r}")

    def fake_text_search(
        repository: object,
        query: str,
        *,
        limit: int,
        source_key: str | None,
    ) -> dict[str, Any]:
        captured.update(repository=repository, query=query, limit=limit, source_key=source_key)
        return {"status": "ok", "mode": "text", "query": query, "results": []}

    monkeypatch.setattr(mcp_service, "build_embedding_provider", fail_if_called)
    monkeypatch.setattr(mcp_service, "text_search", fake_text_search)

    result = _service().search("query", mode=" TEXT ", source_key="medium-export", limit=0)

    assert result["status"] == "ok"
    assert result["mode"] == "text"
    assert captured["limit"] == 1
    assert captured["source_key"] == "medium-export"


@pytest.mark.parametrize("mode", ["semantic", "local", "global"])
def test_search_dispatches_embedding_modes_with_configured_retrieval(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    provider = object()
    captured: dict[str, object] = {}

    def fake_build_embedding_provider(settings: object) -> object:
        captured["settings"] = settings
        return provider

    def fake_search(repository: object, query: str, **kwargs: object) -> dict[str, Any]:
        captured.update(repository=repository, query=query, **kwargs)
        if mode == "local":
            return {
                "status": "ok",
                "mode": "graphrag-local",
                "query": query,
                "seeds": [],
                "entities": [],
                "related_documents": [],
                "communities": [],
            }
        if mode == "global":
            return {
                "status": "ok",
                "mode": "graphrag-global",
                "query": query,
                "communities": [],
            }
        return {"status": "ok", "mode": mode, "query": query, "results": []}

    monkeypatch.setattr(mcp_service, "build_embedding_provider", fake_build_embedding_provider)
    monkeypatch.setattr(mcp_service, f"{mode}_search", fake_search)

    service = _service()
    result = service.search("query", mode=mode.upper(), source_key="medium-export", limit=99, community_limit=0)

    assert result["status"] == "ok"
    assert captured["settings"] is service.settings
    assert captured["repository"] is not None
    assert captured["query"] == "query"
    assert captured["limit"] == 20
    assert captured["source_key"] == "medium-export"
    assert captured["provider"] is provider
    assert captured["min_similarity"] == pytest.approx(0.42)
    if mode == "global":
        assert captured["community_limit"] == 1
    else:
        assert "community_limit" not in captured


def test_search_formats_local_and_global_document_references(monkeypatch: pytest.MonkeyPatch) -> None:
    provenance = {"source_key": "medium-export", "url": "https://example.com/post"}

    def fake_local_search(repository: object, query: str, **kwargs: object) -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": "graphrag-local",
            "query": query,
            "degraded_components": [],
            "seeds": [{"id": "chunks/c-1", "document_key": "doc-1", "provenance": provenance}],
            "entities": [{"id": "topics/t-1", "kind": "topic", "label": "Topic"}],
            "related_documents": [
                {
                    "document_key": "doc-2",
                    "title": "Related",
                    "weight": 0.8,
                    "provenance": provenance,
                    "payload": "must-not-cross-mcp-boundary",
                },
            ],
            "communities": [{"community_key": "community-1", "summary": "Summary"}],
        }

    def fake_global_search(repository: object, query: str, **kwargs: object) -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": "graphrag-global",
            "query": query,
            "degraded_components": [],
            "communities": [
                {
                    "community_key": "community-1",
                    "summary": "Summary",
                    "documents": [
                        {"document_key": "doc-1", "title": "Document", "score": 0.9, "provenance": provenance},
                    ],
                },
            ],
        }

    monkeypatch.setattr(mcp_service, "local_search", fake_local_search)
    monkeypatch.setattr(mcp_service, "global_search", fake_global_search)

    local = _service().search("query", mode="local")
    global_result = _service().search("query", mode="global")

    assert local["seeds"][0]["resource_uri"] == "kb://documents/doc-1"
    assert local["related_documents"][0]["resource_uri"] == "kb://documents/doc-2"
    assert local["related_documents"][0]["weight"] == pytest.approx(0.8)
    assert "payload" not in local["related_documents"][0]
    assert local["entities"] == [{"id": "topics/t-1", "kind": "topic", "label": "Topic"}]
    assert local["communities"] == [{"community_key": "community-1", "summary": "Summary"}]
    document = global_result["communities"][0]["documents"][0]
    assert document["resource_uri"] == "kb://documents/doc-1"
    assert document["url"] == "https://example.com/post"


def test_search_rejects_unknown_mode_with_complete_allowlist() -> None:
    result = _service().search("query", mode="unknown")

    assert result["status"] == "error"
    assert result["error"] == "invalid_mode"
    assert all(mode in result["message"] for mode in ("text", "semantic", "hybrid", "local", "global"))


def test_search_returns_structured_embedding_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_to_build(settings: object) -> object:
        raise EmbeddingProviderError(f"provider unavailable for {settings!r}")

    monkeypatch.setattr(mcp_service, "build_embedding_provider", fail_to_build)

    result = _service().search("query", mode="semantic")

    assert result["status"] == "error"
    assert result["error"] == "embedding_provider_error"
    assert "provider unavailable" in result["message"]


def test_document_uri_parsing_and_markdown() -> None:
    assert document_resource_uri("doc-1") == "kb://documents/doc-1"
    assert document_key_from_uri("kb://documents/doc-1") == "doc-1"
    assert document_key_from_uri("https://example.com/doc-1") is None

    markdown = document_to_markdown(
        {
            "status": "ok",
            "document_key": "doc-1",
            "title": "Document title",
            "source_key": "medium-export",
            "url": "https://example.com/post",
            "published_at": "2026-06-01T10:00:00Z",
            "text": "Document body",
            "truncated": False,
            "provenance": {
                "raw_snapshot_key": "raw-1",
                "import_run_key": "import-1",
                "medium_post": {"post_id": "abc123abc123"},
            },
        },
    )

    assert "# Document title" in markdown
    assert "raw_snapshot_key: `raw-1`" in markdown
    assert "medium_post_id: `abc123abc123`" in markdown
    assert "Document body" in markdown


def test_get_document_formats_normalized_document(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_document_row(repository: object, document_key: str) -> dict[str, Any]:
        return {
            "document": {
                "_key": document_key,
                "title": "Doc",
                "text": "x" * 2_000,
                "url": "https://example.com/doc",
                "published_at": "2026-01-01T00:00:00Z",
                "source_key": "medium-export",
                "metadata": {
                    "status": "published",
                    "tags": ["safe", 42],
                    "medium_post": {
                        "post_id": "abc123abc123",
                        "local_post_path": "posts/private.html",
                        "archive": {"root": "/private/raw/medium"},
                    },
                    "attachments": [{"local_path": "/private/raw/telegram/photo.jpg"}],
                    "archive": {"ref": "/private/raw/archive.zip"},
                },
            },
            "raw": {"_key": "raw-1", "captured_at": "2026-07-06T00:00:00Z", "payload": "must-not-leak"},
            "raw_edge": {"import_run_key": "import-1"},
            "source_edge": {
                "provenance": {
                    "medium_post": {
                        "post_id": "abc123abc123",
                        "local_post_path": "posts/private.html",
                        "archive": {"root": "/private/raw/medium"},
                    },
                },
            },
        }

    monkeypatch.setattr(mcp_service, "_document_row", fake_document_row)

    result = _service().get_document("doc-1", max_chars=10)
    serialized = json.dumps(result, ensure_ascii=False)

    assert result["status"] == "ok"
    assert result["resource_uri"] == "kb://documents/doc-1"
    assert len(result["text"]) == 1_000
    assert result["truncated"] is True
    assert result["provenance"]["raw_snapshot_key"] == "raw-1"
    assert result["metadata"] == {
        "status": "published",
        "tags": ["safe"],
        "medium_post": {"post_id": "abc123abc123"},
    }
    assert "must-not-leak" not in serialized
    assert "/private/raw" not in serialized
    assert "local_post_path" not in serialized
    assert "attachments" not in serialized
    assert "archive" not in serialized


def test_document_query_projects_raw_snapshot_without_payload() -> None:
    captured: dict[str, str] = {}

    class _AqlClient:
        def aql(self, query: str, bind_vars: dict[str, object]) -> list[dict[str, object]]:
            captured["query"] = query
            captured["document_key"] = str(bind_vars["document_key"])
            return []

    repository = cast(KnowledgeRepository, type("Repository", (), {"client": _AqlClient()})())

    assert mcp_service._document_row(repository, "doc-1") is None
    assert captured["document_key"] == "doc-1"
    assert "payload" not in captured["query"]
    assert "_key: raw_document._key" in captured["query"]
    assert "captured_at: raw_document.captured_at" in captured["query"]


def _legacy_mcp_search_row(document_key: str, visibility: str) -> dict[str, Any]:
    return {
        "id": f"chunks/{document_key}-c0",
        "title": f"{visibility} document",
        "snippet": f"legacy {visibility} snippet",
        "score": 0.9,
        "score_components": {"bm25": 1.0, "vector": 0.8, "graph_boost": None},
        "document_key": document_key,
        "chunk_key": f"{document_key}-c0",
        "provenance": {
            "source_key": "legacy-source",
            "raw_snapshot_key": f"raw-{document_key}",
            "import_run_key": "legacy-import",
            "url": f"https://example.test/{document_key}",
        },
    }


def test_v5_keeps_legacy_mcp_search_visibility_defaults_and_stable_response_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    provider = object()

    def fake_hybrid_search(
        repository: object,
        query: str,
        *,
        limit: int,
        source_key: str | None,
        provider: object,
        min_similarity: float,
    ) -> dict[str, Any]:
        captured.update(
            repository=repository,
            query=query,
            limit=limit,
            source_key=source_key,
            provider=provider,
            min_similarity=min_similarity,
        )
        return {
            "status": "ok",
            "mode": "hybrid",
            "query": query,
            "degraded_components": [],
            "results": [
                _legacy_mcp_search_row("published-doc", "published"),
                _legacy_mcp_search_row("draft-doc", "draft"),
            ],
        }

    monkeypatch.setattr(mcp_service, "build_embedding_provider", lambda settings: provider)
    monkeypatch.setattr(mcp_service, "hybrid_search", fake_hybrid_search)

    service = _service()
    response = service.search("legacy MCP query")

    assert captured == {
        "repository": service.repository,
        "query": "legacy MCP query",
        "limit": 5,
        "source_key": None,
        "provider": provider,
        "min_similarity": pytest.approx(0.42),
    }
    assert set(response) == {"status", "mode", "query", "degraded_components", "results"}
    assert (response["status"], response["mode"], response["query"]) == ("ok", "hybrid", "legacy MCP query")
    assert [result["document_key"] for result in response["results"]] == ["published-doc", "draft-doc"]
    for result in response["results"]:
        assert set(result) == {
            "id",
            "kind",
            "title",
            "snippet",
            "score",
            "score_components",
            "document_key",
            "chunk_key",
            "resource_uri",
            "url",
            "provenance",
        }
        assert set(result["provenance"]) == {
            "source_key",
            "raw_snapshot_key",
            "import_run_key",
            "medium_post",
            "url",
            "captured_at",
        }
        assert (
            not {
                "visibility",
                "includes_drafts",
                "dossier_key",
                "revision_id",
                "writing_id",
            }
            & result.keys()
        )


@pytest.mark.parametrize("document_status", ["published", "draft"])
def test_v5_keeps_legacy_mcp_direct_document_visibility_and_envelope(
    monkeypatch: pytest.MonkeyPatch,
    document_status: str,
) -> None:
    def fake_document_row(repository: object, document_key: str) -> dict[str, Any]:
        return {
            "document": {
                "_key": document_key,
                "title": f"{document_status} document",
                "text": "legacy body",
                "url": "https://example.test/document",
                "published_at": None,
                "source_key": "legacy-source",
                "metadata": {"status": document_status},
            },
            "raw": {"_key": "raw-legacy", "captured_at": "2026-07-12T12:00:00Z"},
            "raw_edge": {"import_run_key": "import-legacy"},
            "source_edge": None,
        }

    monkeypatch.setattr(mcp_service, "_document_row", fake_document_row)

    response = _service().get_document("legacy-document")

    assert set(response) == {
        "status",
        "document_key",
        "resource_uri",
        "title",
        "url",
        "published_at",
        "source_key",
        "text",
        "truncated",
        "metadata",
        "provenance",
    }
    assert response["status"] == "ok"
    assert response["metadata"] == {"status": document_status}
    assert response["text"] == "legacy body"


def test_mcp_registration_remains_exactly_read_only_without_optional_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import knowledge_base.mcp_server as server

    class FakeToolAnnotations:
        def __init__(self, **values: bool) -> None:
            self.readOnlyHint = values["readOnlyHint"]
            self.destructiveHint = values["destructiveHint"]
            self.idempotentHint = values["idempotentHint"]
            self.openWorldHint = values["openWorldHint"]

    class FakeFastMCP:
        def __init__(self, name: str, *, instructions: str) -> None:
            self.name = name
            self.instructions = instructions
            self.tools: dict[str, tuple[Any, FakeToolAnnotations]] = {}
            self.resources: dict[str, tuple[Any, str]] = {}
            self.prompts: dict[str, Any] = {}

        def tool(self, *, annotations: FakeToolAnnotations):
            def register(function):
                self.tools[function.__name__] = (function, annotations)
                return function

            return register

        def resource(self, uri: str, *, mime_type: str):
            def register(function):
                self.resources[uri] = (function, mime_type)
                return function

            return register

        def prompt(self):
            def register(function):
                self.prompts[function.__name__] = function
                return function

            return register

    mcp_package = ModuleType("mcp")
    mcp_server_package = ModuleType("mcp.server")
    mcp_package.__path__ = []  # type: ignore[attr-defined]
    mcp_server_package.__path__ = []  # type: ignore[attr-defined]
    fastmcp_module = ModuleType("mcp.server.fastmcp")
    types_module = ModuleType("mcp.types")
    fastmcp_module.FastMCP = FakeFastMCP  # type: ignore[attr-defined]
    types_module.ToolAnnotations = FakeToolAnnotations  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mcp", mcp_package)
    monkeypatch.setitem(sys.modules, "mcp.server", mcp_server_package)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_module)
    monkeypatch.setitem(sys.modules, "mcp.types", types_module)
    monkeypatch.setattr(server, "create_service", lambda config_path=None: _service())

    app = server.create_mcp_app()

    assert isinstance(app, FakeFastMCP)
    assert set(app.tools) == {
        "kb_search",
        "kb_get_document",
        "kb_graph_neighbors",
        "kb_list_sources",
        "kb_health",
    }
    for function, tool_annotations in app.tools.values():
        assert callable(function)
        assert tool_annotations.readOnlyHint is True
        assert tool_annotations.destructiveHint is False
        assert tool_annotations.idempotentHint is True
        assert tool_annotations.openWorldHint is False
    assert tuple(inspect.signature(app.tools["kb_search"][0]).parameters) == (
        "query",
        "mode",
        "source_key",
        "limit",
        "community_limit",
    )
    assert (
        not {
            "kb_research_build",
            "kb_research_validate",
            "kb_research_handoff",
            "kb_import_output",
            "kb_write_document",
            "kb_ingest",
        }
        & app.tools.keys()
    )
    assert set(app.resources) == {"kb://sources", "kb://documents/{document_key}"}
    assert {mime_type for _, mime_type in app.resources.values()} == {"application/json", "text/markdown"}
    assert set(app.prompts) == {"research_knowledge_base"}


def test_mcp_server_module_imports_without_optional_dependency() -> None:
    import knowledge_base.mcp_server as server

    assert server.SERVER_NAME == "knowledge-base"


def test_mcp_app_can_be_created_when_extra_is_installed() -> None:
    pytest.importorskip("mcp")
    from knowledge_base.mcp_server import create_mcp_app

    app = create_mcp_app()

    assert app is not None


def test_mcp_stdio_server_advertises_read_only_contract() -> None:
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def inspect_server() -> None:
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "knowledge_base.mcp_server"],
            cwd=Path.cwd(),
        )
        async with (
            stdio_client(server) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            initialized = await session.initialize()
            tools = await session.list_tools()
            resources = await session.list_resources()
            templates = await session.list_resource_templates()
            prompts = await session.list_prompts()

        assert initialized.serverInfo.name == "knowledge-base"
        assert {tool.name for tool in tools.tools} == {
            "kb_search",
            "kb_get_document",
            "kb_graph_neighbors",
            "kb_list_sources",
            "kb_health",
        }
        assert all(tool.annotations and tool.annotations.readOnlyHint for tool in tools.tools)
        assert all(tool.annotations and tool.annotations.destructiveHint is False for tool in tools.tools)
        assert {str(resource.uri) for resource in resources.resources} == {"kb://sources"}
        assert {resource.mimeType for resource in resources.resources} == {"application/json"}
        assert {template.uriTemplate for template in templates.resourceTemplates} == {
            "kb://documents/{document_key}",
        }
        assert {template.mimeType for template in templates.resourceTemplates} == {"text/markdown"}
        assert {prompt.name for prompt in prompts.prompts} == {"research_knowledge_base"}

    asyncio.run(inspect_server())


def test_research_prompt_mentions_provenance() -> None:
    prompt = research_prompt("AI-native platforms", source_key="medium-export")

    assert 'source_key="medium-export"' in prompt
    assert "Cite source_key, document_key, URL and raw_snapshot_key" in prompt
