# CLI Contract: Book Cube Owner Archive Import

## `kb ingest book-cube-archive --archive PATH`

Imports Telegram Desktop JSON export directory or zip.

Successful response:

```json
{
  "status": "ok",
  "source_key": "book-cube",
  "import_run_key": "import-book-cube-archive-...",
  "archive": {
    "kind": "directory",
    "ref": "data/raw/book-cube/export",
    "result_json": "data/raw/book-cube/export/result.json",
    "manifest_sha256": "..."
  },
  "created": {
    "sources": 0,
    "raw_snapshots": 1,
    "documents": 1200,
    "chunks": 1800,
    "topics": 45,
    "authors": 0,
    "works": 0,
    "edges": 5000
  },
  "deduplicated": {
    "documents": 0,
    "chunks": 0
  },
  "skipped": []
}
```

Error response:

```json
{
  "status": "error",
  "error": "archive_not_readable",
  "source_key": "book-cube",
  "archive": "data/raw/book-cube/export",
  "hint": "Export Telegram channel as machine-readable JSON and rerun with --archive."
}
```

Allowed error values:

- `archive_not_readable`
- `result_json_not_found`
- `invalid_telegram_export`
