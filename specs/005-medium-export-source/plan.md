# Implementation Plan: Medium Export Source

## Технический контекст

- Используется только Python standard library, как в существующих source adapters.
- ArangoDB schema остается без изменений; используются существующие `sources`, `raw_snapshots`, `documents`, `chunks`, `authors`, `import_runs` и edge collections.
- Raw export files хранятся в gitignored `data/raw/medium/`; в git попадают только synthetic fixtures.
- Retrieval layer уже содержит text, semantic, hybrid и graph entry points; feature расширяет их optional exact `source_key` filtering без schema migration.

## Дизайн ingest

- Добавить `knowledge_base.sources.medium_export`.
- Читать directory или zip, находить export root по `README.html`, собирать `posts/*.html` и считать deterministic manifest hash по relative paths, sizes and sha256 values.
- Хранить raw snapshot payload как manifest JSON со `storage_kind="local_manifest"`.
- Парсить Medium HTML через `html.parser`, извлекая body text из `section[data-field=body]` и provenance metadata из footer microformats.
- Upsert documents/chunks/author edges через deterministic keys и существующие hash embeddings.

## Дизайн retrieval

- `text_search`, `semantic_search`, `hybrid_search` и `graph_neighbors` принимают `source_key: str | None`.
- CLI открывает то же поведение через `--source SOURCE_KEY`.
- Unknown source возвращает successful empty result, без отдельной ошибки.
- `hybrid_search` передает source filter в lexical и semantic branches.
- `graph_neighbors(..., documents_only=True)` returns distinct document-shaped results и не меняет default low-level graph output.

## CLI

- Добавить `kb ingest medium-export --archive PATH`.
- Добавить `--include-drafts` для явного import drafts.
- Добавить `--source` в `kb search text`, `kb search semantic`, `kb search hybrid` и `kb graph neighbors`.
- Добавить `--documents-only` в `kb graph neighbors`.
- Возвращать structured error payloads для unreadable, malformed или post-less archives.

## Приватность

- Не нормализовать profile/session/IP/social folders в v1.
- Не коммитить реальные Medium exports.
- Хранить images and links только как metadata references.
