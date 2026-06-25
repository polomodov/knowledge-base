# Tasks: Production Knowledge Pipeline

**Input**: Design documents from `/specs/001-production-knowledge-pipeline/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

**Tests**: Tests are included because the feature is production-grade infrastructure and must be independently verifiable.

## Phase 1: Setup

**Purpose**: Establish Python package and local runtime skeleton.

- [x] T001 Create Python package structure under `src/knowledge_base/`.
- [x] T002 Add `pyproject.toml` with CLI entrypoint `kb` and test dependencies.
- [x] T003 Add `.gitignore` entries for `.local/`, `data/raw/`, `data/processed/`, `data/generated/` and local secrets.
- [x] T004 Create ArangoDB compose runtime under `compose/`.
- [x] T005 Add safe fixture files under `tests/fixtures/`.

---

## Phase 2: Foundational

**Purpose**: Core infrastructure required before any user story implementation.

- [x] T006 Implement config loading for ArangoDB connection settings.
- [x] T007 Implement ArangoDB client factory and healthcheck.
- [x] T008 Implement idempotent schema bootstrap for collections and edge collections.
- [x] T009 Implement ArangoSearch View and vector index bootstrap.
- [x] T010 Implement deterministic key helpers for source, raw snapshot, document and chunk records.
- [x] T011 Implement base JSON output helpers for CLI commands.

**Checkpoint**: Runtime can be bootstrapped and healthchecked without fixture data.

---

## Phase 3: User Story 1 - Ingest safe fixture with provenance (Priority: P1)

**Goal**: Load fixture data and preserve provenance.

**Independent Test**: `kb ingest fixture` creates source, raw snapshot, document, chunks and provenance edges.

### Tests

- [x] T012 [P] [US1] Add unit tests for deterministic ids.
- [x] T013 [P] [US1] Add integration test for fixture ingest idempotency.

### Implementation

- [x] T014 [US1] Implement source upsert.
- [x] T015 [US1] Implement raw snapshot registration.
- [x] T016 [US1] Implement document and chunk upsert.
- [x] T017 [US1] Implement provenance edge creation.
- [x] T018 [US1] Implement `kb ingest fixture`.

**Checkpoint**: Fixture ingest is repeatable and creates no duplicate canonical records.

---

## Phase 4: User Story 2 - Full-text search with provenance (Priority: P1)

**Goal**: Query ArangoSearch and return BM25-ranked results with provenance.

**Independent Test**: `kb search text "..."` returns fixture chunks with snippet and provenance.

### Tests

- [x] T019 [P] [US2] Add integration test for lexical search hit.
- [x] T020 [P] [US2] Add integration test for no-match query response.

### Implementation

- [x] T021 [US2] Implement text search repository/query.
- [x] T022 [US2] Implement snippet and score mapping.
- [x] T023 [US2] Implement `kb search text`.

**Checkpoint**: Full-text retrieval works independently from vector search.

---

## Phase 5: User Story 3 - Vector search over embeddings (Priority: P2)

**Goal**: Store embeddings and run vector nearest-neighbor retrieval.

**Independent Test**: `kb search semantic "..."` returns nearest fixture chunks when embeddings exist.

### Tests

- [x] T024 [P] [US3] Add unit test for vector dimension validation.
- [x] T025 [P] [US3] Add integration test for semantic search degraded response when embeddings are missing.

### Implementation

- [x] T026 [US3] Implement embedding provider interface with deterministic fixture provider.
- [x] T027 [US3] Implement embedding write/update for chunks.
- [x] T028 [US3] Implement vector search query.
- [x] T029 [US3] Implement `kb search semantic`.

**Checkpoint**: Semantic retrieval works or fails clearly when vector index is not ready.

---

## Phase 6: User Story 4 - Graph traversal for knowledge exploration (Priority: P2)

**Goal**: Traverse source/document/chunk/topic/author/work relationships.

**Independent Test**: `kb graph neighbors --topic ...` returns graph neighbors with relation types.

### Tests

- [x] T030 [P] [US4] Add integration test for topic neighbor traversal.
- [x] T031 [P] [US4] Add integration test for unknown topic response.

### Implementation

- [x] T032 [US4] Implement graph projection from fixture metadata.
- [x] T033 [US4] Implement graph traversal query.
- [x] T034 [US4] Implement `kb graph neighbors`.

**Checkpoint**: Graph layer is queryable independently from hybrid retrieval.

---

## Phase 7: User Story 5 - Hybrid retrieval for GraphRAG (Priority: P3)

**Goal**: Merge text, vector and graph-neighborhood signals into one retrieval result.

**Independent Test**: `kb search hybrid "..."` returns score breakdown and provenance.

### Tests

- [x] T035 [P] [US5] Add integration test for hybrid result schema.
- [x] T036 [P] [US5] Add integration test for degraded mode without vector component.

### Implementation

- [x] T037 [US5] Implement score normalization and merge logic.
- [x] T038 [US5] Implement graph-neighborhood boost.
- [x] T039 [US5] Validate output against `contracts/query-output.schema.json`.
- [x] T040 [US5] Implement `kb search hybrid`.

**Checkpoint**: Hybrid retrieval returns usable GraphRAG context with provenance.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [x] T041 [P] Update `README.md` quickstart links.
- [x] T042 [P] Update `docs/architecture.md` with implemented ArangoDB architecture.
- [x] T043 Add `kb export jsonl`.
- [x] T044 Run quickstart end-to-end.
- [x] T045 Run full test suite and ADR checks.

## Dependencies & Execution Order

- Setup -> Foundational -> US1 -> US2.
- US3 and US4 can start after US1.
- US5 depends on US2 plus either US3 or degraded-mode support.
- Polish happens after selected stories are complete.

## Parallel Opportunities

- T012/T013, T019/T020, T024/T025, T030/T031 and T035/T036 can be parallelized.
- US3 and US4 can proceed in parallel after fixture ingest exists.

## Notes

- Every result must include provenance.
- No task should require real personal data.
- Stop after each checkpoint and validate independently.
