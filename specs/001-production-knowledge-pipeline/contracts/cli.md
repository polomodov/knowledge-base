# CLI Contract: Production Knowledge Pipeline

All commands are planned under the `kb` executable. Commands should accept `--config` and environment variable overrides for ArangoDB connection settings.

## Platform

### `kb platform up`

Starts the local runtime or prints exact instructions when Docker/Colima is unavailable. The local compose runtime starts ArangoDB with the `--vector-index` server flag.

Expected output:

```json
{
  "status": "started",
  "services": {
    "arangodb": "healthy"
  }
}
```

### `kb platform health`

Checks ArangoDB connectivity, database availability, collections, graph, search view and vector index readiness.

Expected output:

```json
{
  "status": "ok",
  "database": "knowledge_base",
  "checks": [
    {"name": "arangodb", "status": "ok"},
    {"name": "collections", "status": "ok"},
    {"name": "arangosearch", "status": "ok"},
    {"name": "vector_index", "status": "ok"}
  ]
}
```

## Ingest

### `kb ingest fixture`

Loads the safe synthetic fixture into ArangoDB.

Expected output:

```json
{
  "status": "ok",
  "import_run_key": "fixture-2026-06-23",
  "created": {
    "sources": 1,
    "raw_snapshots": 1,
    "documents": 1,
    "chunks": 1,
    "edges": 9
  },
  "deduplicated": {
    "documents": 0,
    "chunks": 0
  }
}
```

## Indexing

### `kb index rebuild --target all`

Rebuilds derived indexes/projections. Valid targets: `all`, `text`, `vector`, `graph`.

Expected output:

```json
{
  "status": "ok",
  "index_run_key": "index-run-001",
  "target": "all",
  "counts": {
    "text_indexed": 1,
    "vectors_indexed": 1,
    "graph_edges_checked": 9
  }
}
```

## Query

### `kb search text "query"`

Returns BM25-ranked lexical matches.

### `kb search semantic "query"`

Returns vector nearest-neighbor matches.

### `kb search hybrid "query"`

Returns merged lexical, vector and graph-neighborhood results.

### `kb graph neighbors --topic topic-key`

Returns graph neighbors for a topic/document/chunk/author/work.

Supported start selectors:

```bash
kb graph neighbors --topic systems-thinking
kb graph neighbors --author fixture-author
kb graph neighbors --work fixture-work-knowledge-graphs
kb graph neighbors --document doc-key
kb graph neighbors --chunk chunk-key
```

## Export

### `kb export jsonl --output data/generated/exports/fixture.jsonl`

Exports safe result records with provenance. Real personal data exports must target gitignored paths.
