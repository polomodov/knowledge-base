# CLI Contract: Production Knowledge Pipeline

All commands are planned under the `kb` executable. Commands should accept `--config` and environment variable overrides for ArangoDB connection settings.

## Platform

### `kb platform up`

Starts the local runtime or prints exact instructions when Docker/Colima is unavailable. The local compose runtime starts ArangoDB with the `--vector-index` server flag and binds the port to loopback.

`up` does not wait for or verify health — the container is reported as `starting`. Expected output on success:

```json
{
  "status": "started",
  "services": {"arangodb": "starting"},
  "command": "docker compose --env-file ... -f ... up -d",
  "stdout": "...",
  "stderr": "..."
}
```

`status` is `error` if the compose command exits non-zero, or `unavailable` (with `reason` and `instructions`) when no Docker/Compose is found. The process exits 0 only when `status` is `started`.

### `kb platform health`

Checks ArangoDB connectivity, database availability, collections, graph, search view and vector index readiness.

Expected output:

```json
{
  "status": "ok",
  "database": "knowledge_base",
  "checks": [
    {"name": "arangodb", "status": "ok", "version": "3.12"},
    {"name": "collection:documents", "status": "ok"},
    {"name": "arangosearch", "status": "ok", "view": "kb_text_view"},
    {"name": "graph", "status": "ok", "graph": "knowledge_graph"},
    {"name": "vector_index", "status": "ok", "index": "idx_chunks_embedding_vector"}
  ]
}
```

There is one `collection:<name>` check per collection. `status` is `degraded` when a component is missing (for example the vector index on a build without `--vector-index`) and `error` when ArangoDB is unreachable. The process exits non-zero when a core component (server, collection, view, or graph) is missing; a degraded-only vector index still exits 0 because semantic search falls back to a full scan.

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

Optional source filter:

```bash
kb search text "query" --source medium-export
```

### `kb search semantic "query"`

Returns vector nearest-neighbor matches.

Optional source filter:

```bash
kb search semantic "query" --source medium-export
```

### `kb search hybrid "query"`

Returns merged lexical, vector and graph-neighborhood results.

Optional source filter:

```bash
kb search hybrid "query" --source medium-export
```

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

Optional source filter and distinct document mode:

```bash
kb graph neighbors --author fixture-author --source medium-export --documents-only
```

## Export

### `kb export jsonl --output data/generated/exports/fixture.jsonl`

Exports safe result records with provenance. Real personal data exports must target gitignored paths.
