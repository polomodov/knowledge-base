# Tasks: Tell Me About Tech Source

**Input**: Design documents from `/specs/002-tellmeabout-tech-source/`

## Phase 1: Specification

- [x] T001 Create Spec Kit docs for the source feature.
- [x] T002 Document CLI contract, data mapping and quickstart.

## Phase 2: Parser and Adapter

- [x] T003 Add `knowledge_base.sources` boundary.
- [x] T004 Implement HTML-to-text normalization.
- [x] T005 Implement RSS/Atom feed parsing.
- [x] T006 Implement canonical id, topic and author mapping.
- [x] T007 Implement live fetch failure contract.

## Phase 3: Ingest and CLI

- [x] T008 Implement `ingest_tellmeabout_tech`.
- [x] T009 Add `kb ingest tellmeabout-tech` CLI command.
- [x] T010 Store source/raw/document/chunk/topic/author edges with provenance.
- [x] T011 Rename deterministic embedding provider metadata to `hash-v1`.

## Phase 4: Tests

- [x] T012 Add synthetic Medium-like feed fixture.
- [x] T013 Add parser unit tests.
- [x] T014 Add live fetch failure unit test.
- [x] T015 Add ArangoDB integration test for ingest/idempotency/search/graph/hybrid.

## Phase 5: Docs and Validation

- [x] T016 Update README, architecture and roadmap.
- [x] T017 Run unit tests.
- [x] T018 Run integration tests.
- [x] T019 Run ADR/docs sanity checks and `git diff --check`.
