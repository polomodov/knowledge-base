# Feature Specification: Book Cube Owner Archive Import

**Feature Branch**: `004-book-cube-owner-archive-import`

**Created**: 2026-07-06

**Status**: Draft

**Input**: User description: "Make a script to import data from the Telegram channel archive I can download fully as the channel owner."

**EN summary**: Extend the Book Cube source adapter to import a full owner Telegram Desktop JSON archive from a directory or zip, preserving text/caption, hashtags, attachments metadata, raw provenance and idempotency.

## User Scenarios & Testing

### User Story 1 - Import Owner Archive Directory (Priority: P1)

Как владелец канала, я хочу указать папку Telegram Desktop export, чтобы импортировать все опубликованные сообщения с текстом или caption.

**Independent Test**: выполнить `kb ingest book-cube-archive --archive tests/fixtures/book_cube_owner_export` и проверить source, raw snapshot, documents, chunks, topics, attachment metadata and provenance.

### User Story 2 - Import Owner Archive Zip (Priority: P1)

Как оператор пайплайна, я хочу импортировать `.zip` export без ручной распаковки.

**Independent Test**: создать synthetic zip с nested `result.json`, выполнить ingest и проверить ту же нормализацию сообщений.

### User Story 3 - Use Imported Archive In Retrieval (Priority: P2)

Как исследователь, я хочу искать по архивным сообщениям и ходить по hashtag topics.

**Independent Test**: после archive ingest выполнить text search, graph neighbors by topic and hybrid search with provenance.

## Requirements

- **FR-001**: System MUST add CLI `kb ingest book-cube-archive --archive PATH`.
- **FR-002**: System MUST accept archive directory or `.zip`.
- **FR-003**: System MUST find `result.json` in root or common nested paths.
- **FR-004**: System MUST compute sha256 for `result.json` and archive manifest hash.
- **FR-005**: System MUST import `type == "message"` entries with text, text entities, captions or mixed arrays.
- **FR-006**: System MUST skip service, unsupported and media-only empty messages with stable reasons.
- **FR-007**: System MUST store attachment metadata references only, not binary payloads.
- **FR-008**: System MUST preserve provenance: archive ref, result json ref, message id, raw snapshot and import run.
- **FR-009**: System MUST keep real owner archives under gitignored `data/raw/book-cube/`.
- **FR-010**: System MUST keep existing `kb ingest book-cube --input` behavior.

## Success Criteria

- **SC-001**: Synthetic owner archive directory imports and reruns without duplicate canonical documents/chunks.
- **SC-002**: Synthetic zip archive imports with the same normalized messages.
- **SC-003**: Imported documents include `metadata.attachments[]` for media/file references.
- **SC-004**: Retrieval results include provenance for archive-imported messages.
- **SC-005**: No real archive or binary media files are required in git.

## Assumptions

- Full archive workflow uses Telegram Desktop Machine-readable JSON export.
- HTML export remains supported only through the existing single snapshot command.
- Media binaries stay outside ArangoDB and git.
- No Telegram auth, API client, bot token, LLM enrichment or binary media indexing in this slice.
