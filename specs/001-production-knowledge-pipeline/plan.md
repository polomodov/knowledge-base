# Implementation Plan: Production Knowledge Pipeline

**Branch**: `001-production-knowledge-pipeline` | **Date**: 2026-06-23 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-production-knowledge-pipeline/spec.md`

## Summary

Build a production-like local knowledge pipeline centered on ArangoDB. ArangoDB stores canonical documents, chunks, graph edges, ArangoSearch full-text indexes and vector indexes. Python CLI commands provide fixture ingest, schema/index bootstrap, reindex, text/vector/graph/hybrid query and export. MinIO and Dagster are designed as optional extensions, not required for the first fixture slice.

## Technical Context

**Language/Version**: Python 3.14 for local development; implementation should target Python >=3.12 unless dependency support requires narrowing.

**Primary Dependencies**: Python standard library for CLI/HTTP/TOML, pytest for tests. Optional later: ArangoDB Python driver, Typer/Click, Pydantic, Dagster, MinIO client, local embedding runtime.

**Storage**: ArangoDB as primary multi-model store. Optional MinIO for large raw/binary payloads after v1.

**Testing**: pytest unit/integration tests; CLI quickstart validation against local ArangoDB runtime.

**Target Platform**: macOS local development with Docker/Colima or Docker Desktop; Linux-compatible compose runtime.

**Project Type**: Python CLI/library plus local service runtime.

**Performance Goals**: Fixture pipeline should complete in under 60 seconds locally; search and graph queries over fixture data should return in under 2 seconds.

**Constraints**: Real personal data must stay outside git; ingest/reindex must be idempotent; every retrieval result must include provenance.

**Scale/Scope**: v1 targets a synthetic fixture and the first real personal source later; architecture should tolerate tens of thousands of chunks before split-out decisions.

## Constitution Check

Current constitution is still the default Spec Kit template. Apply project invariants from `AGENTS.md` and ADRs for this feature:

- Provenance is required for every imported item and every retrieval result.
- Raw, processed and generated data stay separate.
- Specs, plans and tasks are project artifacts, not knowledge data.
- Real personal data must not be committed.
- Pipelines must be reproducible and idempotent.

## Project Structure

### Documentation (this feature)

```text
specs/001-production-knowledge-pipeline/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── cli.md
│   └── query-output.schema.json
└── tasks.md
```

### Source Code (repository root)

```text
compose/
└── arangodb.compose.yml

config/
├── arangodb.env.example
└── pipeline.example.toml

src/
└── knowledge_base/
    ├── cli/
    ├── arango.py
    ├── schema.py
    ├── fixture.py
    ├── indexing.py
    ├── retrieval.py
    ├── exporting.py
    └── platform.py

tests/
├── unit/
├── integration/
└── fixtures/
```

**Structure Decision**: Use one Python package with small modules for platform bootstrap, ingest, indexing, retrieval, graph projection and export. Keep service runtime under `compose/` and safe configuration examples under `config/`.

## Runtime Design

- ArangoDB starts as the only required runtime service.
- Database bootstrap creates collections, edge collections, graph definition, ArangoSearch View(s), vector indexes and operational indexes.
- CLI commands should work with `KB_ARANGO_URL`, `KB_ARANGO_DATABASE`, `KB_ARANGO_USER`, `KB_ARANGO_PASSWORD` and config file fallbacks.
- Runtime state and real data live under ignored local paths.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| Multi-model database with search/vector/graph in v1 | The goal is to test a production-grade retrieval pipeline, not only storage | Plain files/SQLite would not exercise GraphRAG, hybrid retrieval or vector indexes |
| Optional orchestration design before Dagster implementation | Asset lineage and retries matter for production-grade pipeline | Implementing Dagster immediately would slow the first end-to-end fixture |
