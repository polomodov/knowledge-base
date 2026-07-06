# Tasks: Book Cube Owner Archive Import

**Input**: Design documents from `/specs/004-book-cube-owner-archive-import/`

## Phase 1: Specification

- [x] T001 Create Spec Kit docs for owner archive import.
- [x] T002 Document CLI contract, data mapping and quickstart.

## Phase 2: Archive Reader

- [x] T003 Add archive directory discovery for `result.json`.
- [x] T004 Add zip discovery for nested `result.json`.
- [x] T005 Compute result sha256 and manifest sha256.
- [x] T006 Build attachment metadata references without copying binaries.

## Phase 3: Ingest and CLI

- [x] T007 Extend Telegram JSON parser for captions and attachments.
- [x] T008 Implement `ingest_book_cube_archive`.
- [x] T009 Add `kb ingest book-cube-archive --archive`.
- [x] T010 Store archive metadata in raw snapshot, document metadata and provenance.

## Phase 4: Tests

- [x] T011 Add synthetic owner archive directory fixture.
- [x] T012 Add unit tests for directory and zip archive discovery.
- [x] T013 Add unit tests for captions, attachments and skipped messages.
- [x] T014 Add ArangoDB integration test for directory/zip archive import.

## Phase 5: Docs and Validation

- [x] T015 Update README, architecture, roadmap and source spec links.
- [x] T016 Run unit tests.
- [x] T017 Run integration tests.
- [x] T018 Run ADR/docs sanity checks and `git diff --check`.
