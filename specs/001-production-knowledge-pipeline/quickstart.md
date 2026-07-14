# Quickstart: Production Knowledge Pipeline

This quickstart is the reproducible validation path for the implemented first slice. It uses safe synthetic fixture data only.

## Prerequisites

Install a local container runtime:

```bash
brew install colima docker docker-compose
colima start --cpu 4 --memory 8 --disk 60
```

Alternative: Docker Desktop.

## Start runtime

```bash
uv run kb platform up
uv run kb platform bootstrap
uv run kb platform health
```

Expected:

- ArangoDB is reachable.
- Database exists.
- Required collections, edge collections, graph definition, ArangoSearch View and vector index are ready.

## Load fixture

```bash
uv run kb ingest fixture
```

Expected:

- One source is created.
- One raw snapshot is registered.
- One document and one or more chunks are created.
- Provenance edges connect chunks/documents back to raw/source.

## Rebuild indexes

```bash
uv run kb index rebuild --target all
```

Expected:

- Text index is ready.
- Vector index is ready when fixture embeddings exist.
- Graph edges are checked.

## Query

```bash
uv run kb search text "systems thinking"
uv run kb search semantic "how ideas connect across books"
uv run kb graph neighbors --topic systems-thinking
uv run kb graph neighbors --author fixture-author
uv run kb graph neighbors --work fixture-work-knowledge-graphs
uv run kb search hybrid "systems thinking and writing workflow"
```

Every result must include provenance.

## Export

```bash
uv run kb export jsonl --output data/generated/exports/fixture.jsonl
```

The export path is under generated data and must remain outside git for real personal data.
