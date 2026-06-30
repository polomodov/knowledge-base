# CLI Contract: Book Cube Telegram Source

## `kb ingest book-cube`

Attempts to fetch the default public preview URL `https://t.me/s/book_cube`.

## `kb ingest book-cube --url URL`

Attempts to fetch a custom public preview URL. On timeout, network failure or HTTP error, returns:

```json
{
  "status": "error",
  "error": "live_fetch_unavailable",
  "source_key": "book-cube",
  "url": "https://t.me/s/book_cube",
  "hint": "Save Telegram HTML/JSON export under data/raw/book-cube/ and rerun with --input."
}
```

## `kb ingest book-cube --input PATH`

Reads a local Telegram HTML or JSON snapshot and imports it.

```json
{
  "status": "ok",
  "source_key": "book-cube",
  "import_run_key": "import-book-cube-...",
  "input": {
    "kind": "file",
    "ref": "tests/fixtures/book_cube_channel.html",
    "sha256": "..."
  },
  "created": {
    "sources": 1,
    "raw_snapshots": 1,
    "documents": 2,
    "chunks": 2,
    "topics": 3,
    "authors": 0,
    "works": 0,
    "edges": 10
  },
  "deduplicated": {
    "documents": 0,
    "chunks": 0
  },
  "skipped": []
}
```
