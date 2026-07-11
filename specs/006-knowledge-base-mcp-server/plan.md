# Implementation Plan: Knowledge Base MCP Server

## Технический контекст

- MCP SDK подключается как optional extra `mcp`, чтобы обычный CLI `kb` не зависел от SDK.
- Тяжёлый `sentence-transformers` остаётся вне lock-файла по принятому GraphRAG-контракту; для provider `local` quickstart явно требует ручную установку в server environment.
- Транспорт v1: stdio через FastMCP; HTTP/remote mode out of scope.
- Сервер использует существующие `Settings`, `ArangoClient`, `KnowledgeRepository`, configured `EmbeddingProvider` и retrieval functions.

## Дизайн

- `knowledge_base.mcp_service` содержит read-only service functions, formatting, URI helpers and prompt text.
- `knowledge_base.mcp_server` содержит thin FastMCP adapter and `kb-mcp` entrypoint.
- `kb_search` маршрутизирует `text`, `semantic`, `hybrid`, `local`, `global`; embedding-backed режимы используют `build_embedding_provider(settings)` и `settings.retrieval_min_similarity`.
- Tools return JSON-like dictionaries with `status`, result snippets, nested GraphRAG context, resource URIs and allowlisted provenance.
- Resources return source inventory JSON and document Markdown with provenance header.

## Safety

- No ingest/index/export operations in v1.
- No raw snapshot payloads in document responses.
- Clamp user-controlled `limit` and `max_chars`.
- Catch Arango/embedding-provider errors at MCP service boundary and return structured `status="error"` payloads.
- Advertise MCP read-only/non-destructive annotations and explicit resource MIME types.
- Keep synchronous handlers as an accepted v1 local-single-client limitation.

## Docs

- README shows local MCP client command/config.
- Architecture and roadmap mention read-only MCP access.
- ADR 0004 records the local read-only stdio decision.
- CI installs the optional `mcp` extra and covers the stdio handshake plus live isolated integration.
