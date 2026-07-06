# Data Model: Medium Export Source

## MediumExportArchive

- `kind`: `directory` or `zip`
- `ref`: локальный путь к архиву
- `manifest_sha256`: deterministic hash of export manifest
- `total_files`, `total_size_bytes`, `root`

## RawSnapshot

- `source_key`: `medium-export`
- `sha256`: archive manifest hash
- `media_type`: `application/json`
- `storage_kind`: `local_manifest`
- `payload`: manifest JSON с путями файлов, размерами и sha256 values

## MediumPostDocument

- `source_key`: `medium-export`
- `canonical_id`: `medium-post-<medium_post_id>`
- `status`: `published` or `draft`
- `title`, `text`, `url`, `published_at`, `author`
- `metadata.medium_post`: post id, canonical URL, Medium URL, local post path, post sha256, export date and archive manifest metadata
- `metadata.images[]`, `metadata.links[]`: только references, без скачивания binaries

## Provenance

Каждый document/chunk связывается с:

- raw snapshot key
- import run key
- Medium post id
- canonical URL or `medium.com/p/<id>`
- local post path and post sha256
- archive manifest sha256

## Retrieval Result

- `source_key`: exact source identifier, для этой feature `medium-export`
- `raw_snapshot_key`: raw manifest snapshot key
- `import_run_key`: ingest run key
- `medium_post`: post id, canonical URL, local post path, post sha256, export date and archive manifest metadata
- `kind`: `document` for graph document-only mode
- `chunk_key`: `null` for graph document-only mode
