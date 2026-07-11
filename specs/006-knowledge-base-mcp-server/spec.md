# Feature Specification: Knowledge Base MCP Server

**Feature Branch**: `codex/006-knowledge-base-mcp-server`

**Created**: 2026-07-06

**Status**: Complete

**Input**: User description: "Design and implement an MCP server over the knowledge-base for use in other projects."

**EN summary**: Add a local read-only MCP server over the ArangoDB-backed knowledge-base, exposing configured text/semantic/hybrid/local/global retrieval, document expansion, graph neighbors, source inventory, health, resources and a research prompt.

## Пользовательские сценарии и проверка

### User Story 1 - поиск из локального агента (Priority: P1)

Как пользователь других локальных проектов, я хочу подключить `knowledge-base` как MCP server, чтобы агент мог искать по личной базе знаний без прямого доступа к ArangoDB.

**Independent Test**: запустить `uv run --extra mcp kb-mcp --config config/pipeline.local.toml` из MCP-клиента и вызвать `kb_search`.

### User Story 2 - раскрытие найденного документа (Priority: P1)

Как агент, я хочу получить нормализованный документ по `document_key` в пределах безопасного лимита, чтобы использовать найденный результат с provenance и не читать raw export payload.

**Independent Test**: вызвать `kb_get_document` и resource `kb://documents/{document_key}`; проверить title/text/provenance, флаг `truncated` для длинного текста и отсутствие raw snapshot payload.

### User Story 3 - graph context и source filters (Priority: P2)

Как исследователь, я хочу получать document-only graph neighbors и фильтровать результаты по `source_key`, чтобы отделять Medium, Telegram и другие источники.

**Independent Test**: вызвать `kb_graph_neighbors(start_type="author", key="alexander-polomodov", source_key="medium-export", documents_only=true)`.

### User Story 4 - GraphRAG через MCP (Priority: P2)

Как исследователь, я хочу запускать local/global GraphRAG через тот же `kb_search`, чтобы получать локальные подграфы и community-level контекст с цитируемыми документами.

**Independent Test**: вызвать `kb_search(mode="local")` и `kb_search(mode="global", community_limit=5)`; проверить вложенные document resource URI и provenance.

## Требования

- **FR-001**: System MUST add optional dependency extra `mcp` with `mcp>=1.27,<2`.
- **FR-002**: System MUST add script `kb-mcp` that starts a stdio MCP server.
- **FR-003**: MCP server MUST expose read-only tools: `kb_search`, `kb_get_document`, `kb_graph_neighbors`, `kb_list_sources`, `kb_health`.
- **FR-004**: MCP server MUST expose resources `kb://sources` and `kb://documents/{document_key}`.
- **FR-005**: MCP server MUST expose prompt `research_knowledge_base(topic, source_key=None)`.
- **FR-006**: MCP results MUST preserve provenance fields: `source_key`, `raw_snapshot_key`, `import_run_key`, `medium_post`, URL, `document_key`, `chunk_key`.
- **FR-007**: MCP tools MUST clamp `limit` to `1..20` and document `max_chars` to `1000..50000`.
- **FR-008**: MCP v1 MUST NOT expose ingest, index rebuild, export, raw snapshot payloads, local archive/file paths or HTTP transport.
- **FR-009**: `kb_search` MUST support `text`, `semantic`, `hybrid`, `local` and `global`; all embedding-backed modes MUST use the configured embedding provider and `retrieval.min_similarity`.
- **FR-010**: `community_limit` MUST be clamped to `1..20`, and nested local/global document references MUST include `kb://documents/{document_key}`.
- **FR-011**: MCP tools MUST advertise read-only, non-destructive annotations; resources MUST declare JSON/Markdown MIME types.

## Критерии успеха

- **SC-001**: Unit tests cover argument normalization, configured retrieval dispatch, URI helpers, nested result formatting, prompt text, optional server import and stdio capability discovery.
- **SC-002**: Integration tests call all five search modes and service functions over an isolated live ArangoDB fixture/Medium database, verify source filtering and prove collection counts do not change.
- **SC-003**: `kb_search` returns agent-ready snippets with `kb://documents/{document_key}` resource URIs.
- **SC-004**: `kb_get_document` returns normalized document content and does not expose raw payload.
- **SC-005**: Existing CLI/retrieval tests continue to pass without installing `--extra mcp`.

## Допущения

- v1 is local-only stdio and read-only.
- Remote HTTP MCP, auth, audit, write operations and multi-user deployment are future features.
- MCP follows the configured embedding provider/model/dimension and does not build or mutate indexes.
- The optional `mcp` extra does not install the intentionally-unlocked heavy `sentence-transformers` dependency; local embedding users install it explicitly as documented by the core GraphRAG workflow.
