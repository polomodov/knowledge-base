from __future__ import annotations

import argparse
import sys
from typing import Any, Literal

from knowledge_base.arango import ArangoClient
from knowledge_base.config import load_settings
from knowledge_base.mcp_service import KnowledgeBaseMCPService, research_prompt
from knowledge_base.repository import KnowledgeRepository

SERVER_NAME = "knowledge-base"
SERVER_INSTRUCTIONS = """
Read-only access to the personal knowledge-base.
Use tools for search, document expansion, graph neighbors, source inventory and health checks.
Always preserve provenance when using retrieved content.
""".strip()


def create_service(config_path: str | None = None) -> KnowledgeBaseMCPService:
    settings = load_settings(config_path)
    repository = KnowledgeRepository(ArangoClient(settings))
    return KnowledgeBaseMCPService(repository=repository, settings=settings)


def create_mcp_app(config_path: str | None = None) -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.types import ToolAnnotations
    except ModuleNotFoundError as error:  # pragma: no cover - exercised by CLI boundary
        if error.name != "mcp":
            raise
        raise RuntimeError("MCP extra is not installed. Run with: uv run --extra mcp kb-mcp") from error

    service = create_service(config_path)
    app = FastMCP(SERVER_NAME, instructions=SERVER_INSTRUCTIONS)
    read_only = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )

    @app.tool(annotations=read_only)
    def kb_search(
        query: str,
        mode: Literal["text", "semantic", "hybrid", "local", "global"] = "hybrid",
        source_key: str | None = None,
        limit: int = 5,
        community_limit: int = 5,
    ) -> dict[str, Any]:
        """Search documents with text, semantic, hybrid, local or global GraphRAG retrieval."""
        return service.search(
            query,
            mode=mode,
            source_key=source_key,
            limit=limit,
            community_limit=community_limit,
        )

    @app.tool(annotations=read_only)
    def kb_get_document(document_key: str, max_chars: int = 12_000) -> dict[str, Any]:
        """Return one normalized document with metadata and provenance."""
        return service.get_document(document_key, max_chars=max_chars)

    @app.tool(annotations=read_only)
    def kb_graph_neighbors(
        start_type: Literal["topic", "author", "work", "document", "chunk"],
        key: str,
        source_key: str | None = None,
        documents_only: bool = True,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Return graph neighbors for a topic, author, work, document or chunk."""
        return service.graph_neighbors(
            start_type=start_type,
            key=key,
            source_key=source_key,
            documents_only=documents_only,
            limit=limit,
        )

    @app.tool(annotations=read_only)
    def kb_list_sources() -> dict[str, Any]:
        """Return source inventory with approximate normalized document counts."""
        return service.list_sources()

    @app.tool(annotations=read_only)
    def kb_health() -> dict[str, Any]:
        """Return ArangoDB and schema health for the knowledge-base runtime."""
        return service.health()

    @app.resource("kb://sources", mime_type="application/json")
    def kb_sources_resource() -> str:
        """Return source inventory as JSON."""
        return service.sources_resource()

    @app.resource("kb://documents/{document_key}", mime_type="text/markdown")
    def kb_document_resource(document_key: str) -> str:
        """Return one normalized document as Markdown with provenance header."""
        return service.document_resource(document_key)

    @app.prompt()
    def research_knowledge_base(topic: str, source_key: str | None = None) -> str:
        """Plan a provenance-preserving research pass over the knowledge-base."""
        return research_prompt(topic, source_key=source_key)

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kb-mcp", description="Read-only MCP server for knowledge-base")
    parser.add_argument("--config", help="Optional TOML config path")
    args = parser.parse_args(argv)

    try:
        app = create_mcp_app(args.config)
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    app.run(transport="stdio")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
