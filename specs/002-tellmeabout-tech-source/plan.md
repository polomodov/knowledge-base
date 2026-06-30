# Implementation Plan: Tell Me About Tech Source

**Branch**: `002-tellmeabout-tech-source` | **Date**: 2026-06-25 | **Spec**: [spec.md](spec.md)

## Summary

Add a reproducible source adapter for `tellmeabout.tech`. The adapter accepts a Medium-like RSS/Atom feed from either a live URL or a local XML snapshot, normalizes public posts into the existing ArangoDB data model, and reuses the current search/vector/graph pipeline.

## Technical Context

**Language/Version**: Python >=3.12.

**Dependencies**: Python standard library only for feed fetch/parse; pytest for tests.

**Storage**: Existing ArangoDB collections and edge collections from feature 001.

**Runtime**: Current `kb` CLI and ArangoDB Compose runtime.

**Constraints**: Real snapshots stay outside git; no anti-bot bypass; idempotent ingest; provenance on every item.

## Design

- Add `knowledge_base.sources` package with shared normalized item dataclasses and source-specific adapter code.
- Parse RSS 2.0 and Atom variants using `xml.etree.ElementTree`; extract title, link, guid/id, publication date, author, categories/tags and HTML content.
- Convert post HTML to plain text with a deterministic standard-library HTML parser.
- Persist feed payload as one raw snapshot and each valid post as a normalized document/chunks/topics/edges.
- Use deterministic `hash-v1` embeddings for chunks until a real embedding provider is introduced.
- Add CLI command `kb ingest tellmeabout-tech` with `--input` and `--feed-url`.

## Project Structure

```text
src/knowledge_base/sources/
├── __init__.py
├── contracts.py
└── tellmeabout_tech.py

tests/fixtures/
└── tellmeabout_tech_feed.xml

tests/unit/
└── test_tellmeabout_tech_source.py

tests/integration/
└── test_tellmeabout_tech_pipeline.py
```

## Constitution Check

The Spec Kit constitution is still the upstream template. Apply project rules from `AGENTS.md`: preserve provenance, separate raw/processed/generated, avoid committing real personal data, and keep pipelines reproducible.
