# Implementation Plan: Book Cube Owner Archive Import

**Branch**: `004-book-cube-owner-archive-import` | **Date**: 2026-07-06 | **Spec**: [spec.md](spec.md)

## Summary

Add owner-archive ingestion on top of the existing Book Cube Telegram source adapter. The new path reads a Telegram Desktop JSON export from a directory or zip, records raw provenance and archive manifest metadata, imports text/caption messages, and stores attachment references as document metadata only.

## Technical Context

**Language/Version**: Python >=3.12.

**Dependencies**: Python standard library only (`json`, `zipfile`, `pathlib`, `hashlib`); pytest for tests.

**Storage**: Existing ArangoDB collections and edge collections.

**Runtime**: Current `kb` CLI and ArangoDB Compose runtime.

## Design

- Extend `knowledge_base.sources.book_cube` with archive reader functions and `ingest_book_cube_archive`.
- Keep single snapshot ingestion unchanged.
- Treat directory and zip archives as local raw sources; do not copy or load binaries into ArangoDB.
- Store attachment references in document metadata and provenance.
- Add CLI command `kb ingest book-cube-archive --archive PATH`.

## Project Structure

```text
specs/004-book-cube-owner-archive-import/
tests/fixtures/book_cube_owner_export/result.json
tests/unit/test_book_cube_archive.py
tests/integration/test_book_cube_archive_pipeline.py
```

## Constitution Check

Apply project rules from `AGENTS.md`: real raw archives stay out of git, provenance is mandatory, raw/processed/generated zones remain separate, and workflows must be reproducible.
