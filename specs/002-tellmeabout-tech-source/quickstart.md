# Quickstart: Tell Me About Tech Source

## Start Runtime

```bash
uv run kb platform up
uv run kb platform bootstrap
uv run kb platform health
```

## Try Live Feed

```bash
uv run kb ingest tellmeabout-tech --feed-url https://tellmeabout.tech/feed
```

If the site blocks automated access, save a RSS/Medium export under `data/raw/tellmeabout-tech/` and use local input.

## Ingest Local Snapshot

```bash
uv run kb ingest tellmeabout-tech --input data/raw/tellmeabout-tech/feed.xml
uv run kb index rebuild --target all
```

## Query

```bash
uv run kb search text "known phrase from the blog"
uv run kb graph neighbors --topic product-thinking
uv run kb search hybrid "technology writing systems"
```

Every result must include source/raw/import provenance.

## Tests

```bash
uv run --extra test pytest tests/unit/test_tellmeabout_tech_source.py
KB_RUN_INTEGRATION=1 uv run --extra test pytest tests/integration/test_tellmeabout_tech_pipeline.py
```
