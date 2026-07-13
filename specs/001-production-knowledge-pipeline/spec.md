# Feature Specification: Production Knowledge Pipeline

**Feature Branch**: `001-production-knowledge-pipeline`

**Created**: 2026-06-23

**Status**: Complete

**Input**: User description: "ArangoDB-centered production pipeline with full-text search, vector search, graph traversal, provenance, GraphRAG, and hybrid retrieval"

**EN summary**: Design a production-like local knowledge pipeline centered on ArangoDB as the multi-model core for documents, graph relations, full-text search, vector search, and hybrid retrieval.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ingest safe fixture with provenance (Priority: P1)

Как владелец базы знаний, я хочу загрузить безопасный fixture-документ и увидеть, что он сохранен с raw provenance, source metadata, normalized document и chunks.

**Why this priority**: Без корректного ingest/provenance остальной search, vector и graph pipeline не имеет источника истины.

**Independent Test**: На пустой локальной базе выполнить fixture ingest и проверить, что созданы source, raw snapshot, document, chunks и provenance pointers без реальных персональных данных.

**Acceptance Scenarios**:

1. **Given** empty ArangoDB database, **When** fixture ingest runs, **Then** source, raw snapshot, document, chunks and provenance metadata exist.
2. **Given** the same fixture is ingested twice, **When** ingest runs again, **Then** canonical document and chunks are not duplicated.

---

### User Story 2 - Full-text search with provenance (Priority: P1)

Как исследователь, я хочу выполнить полнотекстовый запрос по chunks/documents и получить BM25-ranked результаты со snippet, source и provenance.

**Why this priority**: Полнотекстовый поиск нужен раньше embeddings, потому что он проверяет нормализацию текста и позволяет диагностировать pipeline.

**Independent Test**: После fixture ingest выполнить lexical query с известным термином и убедиться, что результат содержит score, snippet и ссылку на source/raw provenance.

**Acceptance Scenarios**:

1. **Given** indexed fixture chunks, **When** user searches for a lexical term from the fixture, **Then** matching chunks are returned with BM25 score components and provenance.
2. **Given** a query with no lexical match, **When** text search runs, **Then** the response is empty but structurally valid.

---

### User Story 3 - Vector search over embeddings (Priority: P2)

Как автор/исследователь, я хочу выполнять semantic search по chunk embeddings, чтобы находить близкие смысловые фрагменты даже без точного совпадения слов.

**Why this priority**: Semantic retrieval нужен для RAG и исследования, но он зависит от ingest/chunking и embedding generation.

**Independent Test**: На fixture corpus с deterministic local embeddings выполнить semantic query и проверить, что ближайший chunk возвращается с vector score и provenance.

**Acceptance Scenarios**:

1. **Given** chunks with embeddings, **When** semantic search runs, **Then** nearest chunks are returned with vector score and provenance.
2. **Given** embeddings are missing, **When** semantic search runs, **Then** the system returns a clear "index not ready" error and does not corrupt state.

---

### User Story 4 - Graph traversal for knowledge exploration (Priority: P2)

Как исследователь, я хочу увидеть связи между source, document, chunks, topics, authors и works, чтобы исследовать knowledge graph.

**Why this priority**: Graph layer нужен для GraphRAG, topic exploration и проверки, что extracted relations не теряют provenance.

**Independent Test**: После fixture ingest выполнить graph query от topic или document и получить связанных neighbors с типами edge и provenance.

**Acceptance Scenarios**:

1. **Given** fixture relations, **When** user asks for topic neighbors, **Then** related documents/chunks/authors/works are returned with edge types.
2. **Given** an unknown topic id, **When** graph traversal runs, **Then** the response is empty but valid.

---

### User Story 5 - Hybrid retrieval for GraphRAG (Priority: P3)

Как пользователь writing/research workflow, я хочу получить hybrid retrieval result, объединяющий BM25, vector score и graph-neighborhood boost, чтобы использовать его как проверяемый RAG context.

**Why this priority**: Hybrid retrieval объединяет все слои, но должен появиться после базовых text/vector/graph slices.

**Independent Test**: На fixture corpus выполнить hybrid query и проверить, что результат содержит score breakdown и provenance для каждого item.

**Acceptance Scenarios**:

1. **Given** text, vector and graph indexes are ready, **When** hybrid query runs, **Then** results include score components, source references and provenance.
2. **Given** vector index is unavailable, **When** hybrid query runs in degraded mode, **Then** it returns text+graph results and marks vector component as unavailable.

### Edge Cases

- Reindex is interrupted midway.
- ArangoDB is reachable but index/view creation partially failed.
- Fixture contains duplicate canonical ids.
- Chunk text is empty after normalization.
- Embedding vector dimension does not match configured vector index.
- Raw payload is too large for inline storage and must be represented by metadata/object pointer.
- Graph edge references a missing document/chunk/topic.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST store source, raw snapshot, normalized document, chunk and provenance records in ArangoDB collections.
- **FR-002**: System MUST maintain graph edge collections for document/source, chunk/document, topic, author, work and raw provenance relations.
- **FR-003**: System MUST create ArangoSearch View(s) for full-text retrieval over document and chunk text.
- **FR-004**: System MUST create vector index(es) for chunk embeddings when embeddings are present.
- **FR-005**: System MUST provide CLI contracts for platform health, fixture ingest, index rebuild, text search, semantic search, graph query, hybrid search and export.
- **FR-006**: System MUST make ingest and reindex idempotent for the same fixture/canonical ids.
- **FR-007**: System MUST return provenance and source references for every search, graph and hybrid result.
- **FR-008**: System MUST keep real personal raw data outside git; repository fixtures must be safe synthetic examples.
- **FR-009**: System MUST support degraded hybrid retrieval when one optional index component is unavailable.
- **FR-010**: System MUST document optional MinIO and Dagster roles without requiring them for the first local fixture.

### Key Entities *(include if feature involves data)*

- **Source**: Origin of imported knowledge, such as channel, blog, local archive or export.
- **RawSnapshot**: Captured source payload or pointer to external object storage.
- **Document**: Normalized knowledge item with text, metadata, language, status and canonical id.
- **Chunk**: Retrieval unit derived from a document.
- **Topic**: Manual or extracted concept connected to documents/chunks.
- **Author**: Person/entity associated with works or documents.
- **Work**: Book, article, post or other referenced work.
- **ImportRun**: Reproducible ingest execution.
- **IndexRun**: Reproducible indexing/projection execution.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Fresh local runtime can ingest the safe fixture and create all required collections, indexes and graph edges from one documented quickstart.
- **SC-002**: Re-running fixture ingest and all index rebuilds produces no duplicate canonical documents or chunks.
- **SC-003**: Full-text, semantic, graph and hybrid queries return structurally valid JSON with provenance for every result.
- **SC-004**: If vector index is missing, hybrid retrieval still returns text+graph results with an explicit degraded-mode marker.
- **SC-005**: No command in the quickstart requires real personal data or writes private data to git-tracked paths.

## Assumptions

- ArangoDB vector search is acceptable for v1 experimentation; split-out to Qdrant is deferred to a later ADR if needed.
- MinIO is optional in v1 unless raw snapshots become too large or binary-heavy.
- Dagster is optional but recommended after the first CLI-based fixture pipeline works.
- Docker/Colima or Docker Desktop is required for local production-like runtime.
- The first implementation uses synthetic fixtures only.
