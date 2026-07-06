# Feature Specification: Book Cube Telegram Source

**Feature Branch**: `003-book-cube-telegram-source`

**Created**: 2026-06-25

**Status**: Draft

**Input**: User description: "Add second data source: Telegram channel 'Книжный куб' (https://t.me/book_cube)."

**EN summary**: Add a Telegram channel source adapter for public `Книжный куб` posts from `t.me/s/book_cube` HTML snapshots or single Telegram Desktop JSON snapshots, preserving provenance and integrating with the existing ArangoDB retrieval pipeline. Full owner archive import is handled separately in [spec 004](../004-book-cube-owner-archive-import/spec.md).

## User Scenarios & Testing

### User Story 1 - Import Telegram Channel Snapshot (Priority: P1)

Как владелец базы знаний, я хочу импортировать публичные посты канала "Книжный куб" из локального snapshot/export, чтобы они стали documents/chunks с provenance.

**Independent Test**: выполнить `kb ingest book-cube --input tests/fixtures/book_cube_channel.html` и проверить source, raw snapshot, documents, chunks, topics and provenance edges.

**Acceptance Scenarios**:

1. **Given** valid Telegram public channel HTML snapshot, **When** ingest runs, **Then** valid text messages become documents/chunks.
2. **Given** the same snapshot is ingested twice, **When** ingest runs again, **Then** canonical documents and chunks are not duplicated.
3. **Given** a single Telegram Desktop JSON snapshot, **When** ingest runs, **Then** text messages are parsed with the same normalized contract.

### User Story 2 - Live Public Preview With Safe Fallback (Priority: P1)

Как оператор пайплайна, я хочу попробовать live public preview URL, но получить понятную ошибку, если Telegram недоступен.

**Independent Test**: выполнить ingest с unreachable URL и проверить structured `live_fetch_unavailable` response без изменений в базе.

### User Story 3 - Search And Graph Book Notes (Priority: P2)

Как исследователь, я хочу искать по импортированным заметкам канала и ходить по hashtags/topics.

**Independent Test**: после ingest выполнить text search, graph neighbors by topic и hybrid search с provenance.

## Requirements

- **FR-001**: System MUST create source `book-cube` with type `telegram_channel`.
- **FR-002**: System MUST parse Telegram public channel HTML snapshots from `t.me/s/book_cube`.
- **FR-003**: System MUST parse single Telegram Desktop JSON export snapshots with `messages`.
- **FR-004**: System MUST normalize post text, URLs, message ids, publication timestamps and hashtags.
- **FR-005**: System MUST derive deterministic canonical ids from Telegram message ids or `data-post`.
- **FR-006**: System MUST map hashtags to `topics`.
- **FR-007**: System MUST skip empty/service/media-only messages without corrupting state.
- **FR-008**: System MUST return structured `live_fetch_unavailable` for blocked/unreachable live preview requests.
- **FR-009**: System MUST not extract works, infer authors or use LLM enrichment in this first slice.
- **FR-010**: System MUST keep real Telegram snapshots in gitignored `data/raw/`.

## Key Entities

- **BookCubeSource**: Source record for the Telegram channel.
- **TelegramSnapshot**: HTML or JSON payload from live preview or local export.
- **TelegramPostDocument**: Normalized public channel message.
- **Topic**: Hashtag converted into topic node.
- **ImportRun**: Reproducible ingest execution with input kind, ref, sha256, counts and skipped items.

## Success Criteria

- **SC-001**: Synthetic Telegram HTML fixture ingests into ArangoDB and creates no duplicate documents/chunks on rerun.
- **SC-002**: Synthetic Telegram JSON export parses through the same adapter contract.
- **SC-003**: Text, graph and hybrid queries return valid results with provenance for imported Telegram posts.
- **SC-004**: Live fetch failures return structured JSON and do not require bypassing Telegram protections.
- **SC-005**: No real `book_cube` raw snapshots are committed to git.

## Assumptions

- First source scope is public channel posts only.
- Canonical input defaults to local HTML/JSON snapshot fallback.
- Full owner archive directory/zip import is out of scope here and covered by [Book Cube Owner Archive Import](../004-book-cube-owner-archive-import/spec.md).
- Hashtags are the only automatic topic extraction in this slice.
- No attempt is made to bypass Telegram anti-bot, auth or regional restrictions.
