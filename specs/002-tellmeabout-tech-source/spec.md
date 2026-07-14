# Feature Specification: Tell Me About Tech Source

**Feature Branch**: `002-tellmeabout-tech-source`

**Created**: 2026-06-25

**Status**: Complete

**Input**: User description: "First real data source is https://tellmeabout.tech/, imported as a reproducible Medium-like public blog source."

**EN summary**: Add the first real source adapter for public posts from tellmeabout.tech, with RSS/Atom or local snapshot input, provenance-preserving ingest, idempotency, and compatibility with the existing ArangoDB retrieval pipeline.

## User Scenarios & Testing

### User Story 1 - Import Public Blog Snapshot (Priority: P1)

Как владелец базы знаний, я хочу импортировать публичные посты `tellmeabout.tech` из RSS/Atom snapshot, чтобы получить реальные documents/chunks с provenance.

**Independent Test**: выполнить `kb ingest tellmeabout-tech --input tests/fixtures/tellmeabout_tech_feed.xml` на локальной ArangoDB и проверить source, raw snapshot, documents, chunks, topics и provenance edges.

**Acceptance Scenarios**:

1. **Given** valid Medium-like feed snapshot, **When** ingest runs, **Then** source, raw snapshot, documents, chunks, topics and provenance records are created.
2. **Given** the same snapshot is ingested twice, **When** ingest runs again, **Then** canonical documents and chunks are not duplicated.

### User Story 2 - Live Fetch With Safe Fallback (Priority: P1)

Как оператор пайплайна, я хочу попробовать live feed URL, но получить понятную ошибку, если сайт блокирует автоматический доступ.

**Independent Test**: выполнить ingest с unreachable URL и проверить structured `live_fetch_unavailable` response без изменений в базе.

**Acceptance Scenarios**:

1. **Given** no `--input`, **When** live feed is reachable, **Then** payload is captured as a raw snapshot and ingested.
2. **Given** live feed returns 403, timeout or network error, **When** ingest runs, **Then** CLI returns `live_fetch_unavailable` with hint to use a local snapshot.

### User Story 3 - Search And Graph The Imported Blog (Priority: P2)

Как исследователь, я хочу искать по импортированным постам и ходить по тегам как topics, чтобы использовать блог в retrieval workflow.

**Independent Test**: после ingest выполнить text search, graph neighbors by topic и hybrid search с provenance.

**Acceptance Scenarios**:

1. **Given** imported posts, **When** text search runs for a known phrase, **Then** results include snippets, score components and provenance.
2. **Given** imported categories/tags, **When** graph neighbors runs by topic, **Then** related documents/chunks are returned.

## Requirements

- **FR-001**: System MUST create source `tellmeabout-tech` with type `medium_blog`.
- **FR-002**: System MUST parse RSS 2.0 and Atom-like Medium feed snapshots using standard-library XML parsing.
- **FR-003**: System MUST strip HTML content to stable plain text before creating documents/chunks.
- **FR-004**: System MUST derive deterministic canonical ids from canonical URL path, falling back to feed guid/id.
- **FR-005**: System MUST map feed categories/tags to `topics` and document topic edges.
- **FR-006**: System MUST create authors only when author metadata is present in the feed.
- **FR-007**: System MUST not extract works or use LLM enrichment in the first source slice.
- **FR-008**: System MUST return structured `live_fetch_unavailable` for blocked/unreachable live feed requests.
- **FR-009**: System MUST keep real snapshots in gitignored `data/raw/` or local ArangoDB, while committing only synthetic fixtures.
- **FR-010**: System MUST preserve provenance for every created document, chunk and retrieval result.

## Key Entities

- **TellMeAboutTechSource**: Source record for the public blog.
- **FeedSnapshot**: RSS/Atom XML payload from URL or local file.
- **BlogPostDocument**: Normalized post with title, text, canonical URL, publication date, author and tags.
- **Topic**: Feed category/tag converted into a topic node.
- **ImportRun**: Reproducible run that records input kind, ref, sha256, counts and skipped items.

## Success Criteria

- **SC-001**: Synthetic Medium-like feed fixture ingests into ArangoDB and creates no duplicate documents/chunks on rerun.
- **SC-002**: Text, graph and hybrid queries return valid results with provenance for imported blog posts.
- **SC-003**: Live fetch failures return structured JSON and do not require bypassing Cloudflare/Medium protections.
- **SC-004**: No real `tellmeabout.tech` raw snapshots are committed to git.

## Assumptions

- First source scope is published public posts only.
- Canonical input defaults to RSS/Atom feed with local snapshot fallback.
- No LLM enrichment in the first source adapter.
- No attempt is made to bypass Cloudflare or Medium anti-bot protections.
