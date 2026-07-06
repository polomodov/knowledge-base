# Implementation Plan: Book Cube Telegram Source

**Branch**: `003-book-cube-telegram-source` | **Date**: 2026-06-25 | **Spec**: [spec.md](spec.md)

## Summary

Add a reproducible source adapter for Telegram channel `Книжный куб`. The adapter accepts public `t.me/s/book_cube` HTML snapshots, single Telegram Desktop JSON snapshots, or a live public preview URL, normalizes text messages into the existing ArangoDB data model, and reuses current search/vector/graph retrieval. Full owner archive directory/zip import is covered by [spec 004](../004-book-cube-owner-archive-import/spec.md).

## Technical Context

**Language/Version**: Python >=3.12.

**Dependencies**: Python standard library only for fetch/parse; pytest for tests.

**Storage**: Existing ArangoDB collections and edge collections.

**Runtime**: Current `kb` CLI and ArangoDB Compose runtime.

**Constraints**: Real snapshots stay outside git; no auth/anti-bot bypass; idempotent ingest; provenance on every item.

## Design

- Add source adapter `knowledge_base.sources.book_cube`.
- Parse Telegram public HTML with `html.parser`, using `data-post`, message text blocks, message date links and `<time datetime>`.
- Parse single Telegram Desktop JSON snapshots by reading `messages`, extracting text/text_entities and hashtags.
- Persist payload as one raw snapshot and valid messages as documents/chunks/topics/edges.
- Use deterministic `hash-v1` embeddings.
- Add CLI command `kb ingest book-cube` with `--input` and `--url`.

## Project Structure

```text
src/knowledge_base/sources/
└── book_cube.py

tests/fixtures/
├── book_cube_channel.html
└── book_cube_export.json

tests/unit/
└── test_book_cube_source.py

tests/integration/
└── test_book_cube_pipeline.py
```

## Constitution Check

Apply project rules from `AGENTS.md`: preserve provenance, separate raw/processed/generated, avoid committing real personal data, and keep pipelines reproducible.
