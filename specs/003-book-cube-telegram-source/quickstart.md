# Quickstart: Book Cube Telegram Source

## Start Runtime

```bash
uv run kb platform up
uv run kb platform bootstrap
uv run kb platform health
```

## Try Live Public Preview

```bash
uv run kb ingest book-cube --url https://t.me/s/book_cube
```

If Telegram blocks or times out, save a public channel HTML snapshot or single Telegram Desktop JSON snapshot under `data/raw/book-cube/`.

## Ingest Local Snapshot

```bash
uv run kb ingest book-cube --input data/raw/book-cube/channel.html
uv run kb index rebuild --target all
```

JSON export is also supported:

```bash
uv run kb ingest book-cube --input data/raw/book-cube/result.json
```

For a full owner Telegram Desktop archive directory or `.zip`, use [Book Cube Owner Archive Import](../004-book-cube-owner-archive-import/spec.md):

```bash
uv run kb ingest book-cube-archive --archive data/raw/book-cube/export
```

## Query

```bash
uv run kb search text "known phrase from the channel"
uv run kb graph neighbors --topic books
uv run kb search hybrid "книжные заметки"
```

Every result must include source/raw/import provenance.

## Tests

```bash
uv run --extra test pytest tests/unit/test_book_cube_source.py
KB_RUN_INTEGRATION=1 uv run --extra test pytest tests/integration/test_book_cube_pipeline.py
```
