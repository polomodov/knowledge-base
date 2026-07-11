# Tasks: Knowledge Base MCP Server

## Phase 1: Runtime and read-only boundary

- [x] T001 Add optional dependency extra `mcp` and script `kb-mcp`.
- [x] T002 Add read-only MCP service layer over the existing repository/retrieval API.
- [x] T003 Use the configured embedding provider and retrieval relevance floor for semantic-backed modes.
- [x] T004 Expose text/semantic/hybrid/local/global search, document expansion, graph, source inventory and health.
- [x] T005 Add FastMCP stdio tools/resources/prompt with read-only annotations and MIME types.
- [x] T006 Clamp limits, return structured errors and allowlist result/provenance fields.
- [x] T007 Keep ingest/index/export/raw payload and HTTP operations outside the MCP boundary.

## Phase 2: Tests

- [x] T008 Add unit tests for dispatch, configured provider, formatting, URI helpers, clamping and privacy.
- [x] T009 Add stdio capability-discovery smoke test for tools/resources/prompts and annotations.
- [x] T010 Add isolated live-ArangoDB integration test for all search modes and read-only collection counts.

## Phase 3: Project artifacts

- [x] T011 Add Spec Kit spec/plan/data model/contract/quickstart and ADR 0004.
- [x] T012 Update README, architecture, roadmap and ADR index for the current GraphRAG architecture.
- [x] T013 Regenerate `uv.lock` and run MCP tests in CI with the optional extra installed.

## Phase 4: Validation

- [x] T014 Run ruff check/format, mypy, unit suite, isolated integration suite, coverage, ADR and diff checks.
