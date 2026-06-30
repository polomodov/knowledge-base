# Tasks: Book Cube Telegram Source

**Input**: Design documents from `/specs/003-book-cube-telegram-source/`

## Phase 1: Specification

- [x] T001 Create Spec Kit docs for Telegram source feature.
- [x] T002 Document CLI contract, data mapping and quickstart.

## Phase 2: Parser and Adapter

- [x] T003 Implement `knowledge_base.sources.book_cube`.
- [x] T004 Implement Telegram public HTML parsing.
- [x] T005 Implement Telegram Desktop JSON export parsing.
- [x] T006 Implement canonical id, title and hashtag mapping.
- [x] T007 Implement live fetch failure contract.

## Phase 3: Ingest and CLI

- [x] T008 Implement `ingest_book_cube`.
- [x] T009 Add `kb ingest book-cube` CLI command.
- [x] T010 Store source/raw/document/chunk/topic edges with provenance.

## Phase 4: Tests

- [x] T011 Add synthetic Telegram HTML fixture.
- [x] T012 Add synthetic Telegram JSON fixture.
- [x] T013 Add parser unit tests.
- [x] T014 Add live fetch failure unit test.
- [x] T015 Add ArangoDB integration test for ingest/idempotency/search/graph/hybrid.

## Phase 5: Docs and Validation

- [x] T016 Update README, architecture and roadmap.
- [x] T017 Run unit tests.
- [x] T018 Run integration tests.
- [x] T019 Run ADR/docs sanity checks and `git diff --check`.
