# Feature Specification: Medium Export Source

**Feature Branch**: `005-medium-export-source`

**Created**: 2026-07-06

**Status**: Complete

**Input**: User description: "Import my Medium account export from Downloads into the knowledge base as a reproducible source adapter."

**EN summary**: Add a Medium account export adapter for local HTML export directories or zip files, preserving archive manifest provenance and importing published `posts/*.html` into the existing ArangoDB retrieval pipeline.

## Пользовательские сценарии и проверка

### User Story 1 - импорт опубликованных Medium-статей (Priority: P1)

Как владелец базы знаний, я хочу импортировать опубликованные статьи из Medium account export, чтобы мои тексты стали searchable documents/chunks с provenance.

**Independent Test**: выполнить `kb ingest medium-export --archive tests/fixtures/medium_export` и проверить source, raw manifest snapshot, documents, chunks, author и provenance.

### User Story 2 - безопасное сохранение raw-контекста (Priority: P1)

Как оператор пайплайна, я хочу хранить полный Medium export в `data/raw/`, но не превращать profile/sessions/IP/social data в документы.

**Independent Test**: проверить, что raw snapshot содержит manifest, а parser импортирует только `posts/*.html`; drafts skipped by default.

### User Story 3 - поиск по импортированным Medium-статьям (Priority: P2)

Как исследователь, я хочу искать по Medium-статьям и находить их через автора.

**Independent Test**: после ingest выполнить text search, graph neighbors by author and hybrid search with Medium post provenance.

### User Story 4 - выборки только по Medium-источнику (Priority: P2)

Как исследователь, я хочу явно ограничить search/graph выдачу `source_key=medium-export`, чтобы не смешивать Medium-статьи с Telegram или другими источниками.

**Independent Test**: выполнить `kb search text|semantic|hybrid ... --source medium-export` и `kb graph neighbors --author alexander-polomodov --source medium-export --documents-only`; проверить, что результаты относятся только к Medium и граф возвращает distinct documents.

## Требования

- **FR-001**: System MUST add CLI `kb ingest medium-export --archive PATH [--include-drafts]`.
- **FR-002**: System MUST accept Medium export directory or `.zip`.
- **FR-003**: System MUST validate export by finding `README.html` and `posts/*.html`.
- **FR-004**: System MUST store raw snapshot as archive manifest JSON with paths, sizes and hashes, not full post/profile/session payloads.
- **FR-005**: System MUST import published `posts/*.html` by default and skip drafts with reason `draft_excluded`.
- **FR-006**: System MUST import drafts only when `--include-drafts` is set, marking documents as `status="draft"`.
- **FR-007**: System MUST extract Medium post id, canonical URL, title, text, author, published date, export date, image refs and link refs.
- **FR-008**: System MUST create deterministic canonical ids as `medium-post-<medium_post_id>`.
- **FR-009**: System MUST preserve provenance for documents, chunks and retrieval results.
- **FR-010**: System MUST keep real Medium exports under gitignored `data/raw/medium/`.
- **FR-011**: System MUST support exact `source_key` filter in text, semantic, hybrid and graph retrieval API/CLI.
- **FR-012**: System MUST support graph document-only mode that deduplicates by `document_key` and returns document-shaped results.

## Критерии успеха

- **SC-001**: Synthetic Medium export directory and zip ingest without duplicate documents/chunks on rerun.
- **SC-002**: Default ingest imports published posts and skips drafts.
- **SC-003**: `--include-drafts` imports draft posts as draft documents.
- **SC-004**: Search/graph/hybrid retrieval results include `source_key=medium-export` and `medium_post` provenance.
- **SC-005**: Source-filtered retrieval returns only Medium results; unknown source returns `status=ok` and empty `results`.
- **SC-006**: Graph `--documents-only` by author returns distinct Medium documents without chunk/source nodes.
- **SC-007**: No real Medium export files are required in git.

## Допущения

- v1 does not import `profile`, `sessions`, `ips`, `notes`, `bookmarks`, `claps` or following lists into documents.
- Medium export HTML microformats include `p-author`, `dt-published`, `p-canonical` and/or `medium.com/p/<id>`.
- No network fetch, authentication, LLM enrichment or image downloading is part of this slice.
- Retrieval source filter is exact single-source match; multi-source filters are out of scope.
