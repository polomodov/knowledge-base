# CLI Contract: Tell Me About Tech Source

## `kb ingest tellmeabout-tech`

Attempts to fetch the default feed URL `https://tellmeabout.tech/feed`.

## `kb ingest tellmeabout-tech --feed-url URL`

Attempts to fetch a custom feed URL. On 403, timeout, DNS failure or invalid HTTP response, returns structured error JSON and does not mutate ArangoDB.

```json
{
  "status": "error",
  "error": "live_fetch_unavailable",
  "source_key": "tellmeabout-tech",
  "feed_url": "https://tellmeabout.tech/feed",
  "hint": "Save RSS/Medium export under data/raw/tellmeabout-tech/ and rerun with --input."
}
```

## `kb ingest tellmeabout-tech --input PATH`

Reads a local RSS/Atom XML snapshot and imports it.

```json
{
  "status": "ok",
  "source_key": "tellmeabout-tech",
  "import_run_key": "import-tellmeabout-tech-...",
  "input": {
    "kind": "file",
    "ref": "tests/fixtures/tellmeabout_tech_feed.xml",
    "sha256": "..."
  },
  "created": {
    "sources": 1,
    "raw_snapshots": 1,
    "documents": 2,
    "chunks": 2,
    "topics": 3,
    "authors": 1,
    "works": 0,
    "edges": 12
  },
  "deduplicated": {
    "documents": 0,
    "chunks": 0
  },
  "skipped": []
}
```
