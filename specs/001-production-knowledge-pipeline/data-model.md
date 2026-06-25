# Data Model: Production Knowledge Pipeline

## Collections

### `sources`

Represents an origin of knowledge.

Required attributes:

- `_key`: deterministic source slug.
- `type`: `telegram_channel`, `medium_blog`, `local_archive`, `manual_fixture`, or future value.
- `display_name`
- `created_at`
- `metadata`

### `raw_snapshots`

Represents captured source payload or pointer to external/local object.

Required attributes:

- `_key`: content hash or deterministic import id.
- `source_key`
- `sha256`
- `size_bytes`
- `media_type`
- `storage_kind`: `inline`, `local_file`, `object_store`.
- `storage_uri`
- `captured_at`
- `metadata`

### `documents`

Represents normalized knowledge item.

Required attributes:

- `_key`: deterministic from `source_key` and canonical id.
- `source_key`
- `canonical_id`
- `title`
- `text`
- `language`
- `published_at`
- `url`
- `status`: `draft`, `published`, `archived`, `fixture`.
- `metadata`
- `created_at`
- `updated_at`

### `chunks`

Represents retrieval unit.

Required attributes:

- `_key`: deterministic from document key and chunk ordinal/hash.
- `document_key`
- `ordinal`
- `text`
- `token_count`
- `char_start`
- `char_end`
- `embedding`
- `embedding_model`
- `metadata`

### `topics`

Represents manual or extracted concept.

Required attributes:

- `_key`: normalized topic slug.
- `label`
- `language`
- `description`
- `confidence`
- `metadata`

### `authors`

Represents person/entity associated with works or documents.

Required attributes:

- `_key`
- `display_name`
- `aliases`
- `metadata`

### `works`

Represents book, article, post, essay or referenced work.

Required attributes:

- `_key`
- `title`
- `work_type`
- `authors`
- `published_at`
- `metadata`

### `import_runs`

Represents reproducible ingest execution.

Required attributes:

- `_key`
- `started_at`
- `finished_at`
- `status`
- `command`
- `source_key`
- `input_ref`
- `counts`
- `error`

### `index_runs`

Represents search/vector/graph projection execution.

Required attributes:

- `_key`
- `started_at`
- `finished_at`
- `status`
- `target`: `text`, `vector`, `graph`, `hybrid`, `all`.
- `counts`
- `error`

## Edge Collections

### `document_from_source`

From `documents` to `sources`.

Attributes:

- `import_run_key`
- `provenance`
- `created_at`

### `chunk_of_document`

From `chunks` to `documents`.

Attributes:

- `ordinal`
- `created_at`

### `document_mentions_topic`

From `documents` or `chunks` to `topics`.

Attributes:

- `confidence`
- `method`: `manual`, `rule`, `model`.
- `evidence`
- `created_at`

### `document_mentions_author`

From `documents` or `chunks` to `authors`.

Attributes:

- `confidence`
- `method`
- `evidence`
- `import_run_key`
- `provenance`
- `created_at`

### `document_references_work`

From `documents` or `chunks` to `works`.

Attributes:

- `confidence`
- `reference_type`: `quote`, `mention`, `review`, `note`.
- `evidence`
- `import_run_key`
- `provenance`
- `created_at`

### `chunk_derived_from_raw`

From `chunks` to `raw_snapshots`.

Attributes:

- `document_key`
- `char_start`
- `char_end`
- `import_run_key`

### `item_related_to_item`

From any document-like entity to another document-like entity.

Attributes:

- `relation_type`
- `confidence`
- `method`
- `created_at`

## Search and Indexes

- ArangoSearch View over `documents.text`, `documents.title`, `chunks.text`, `topics.label`, `works.title`.
- BM25 ranking for lexical search.
- Vector index on `chunks.embedding`; local ArangoDB runtime must start with the `--vector-index` server flag.
- Persistent indexes for canonical ids, source keys, import/index run status and document/chunk relationships.
- Graph definition covering source, document, chunk, topic, author, work and raw provenance relationships.

## Invariants

- Every `document` has at least one `document_from_source` edge.
- Every `chunk` has exactly one `chunk_of_document` edge.
- Every search result must be traceable to `source_key`, `document_key`, `chunk_key` when applicable, and provenance.
- Reindexing may update indexes and derived edges, but must not duplicate canonical documents/chunks.
- Generated summaries and drafts must not be written into canonical source collections unless a future ADR defines that boundary.
