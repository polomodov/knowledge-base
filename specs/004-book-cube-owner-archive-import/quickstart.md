# Quickstart: Book Cube Owner Archive Import

## Export Telegram Archive

Use Telegram Desktop export for the channel and choose machine-readable JSON. Place the export under gitignored raw data:

```text
data/raw/book-cube/export/result.json
data/raw/book-cube/export/photos/...
data/raw/book-cube/export/files/...
```

## Import Directory

```bash
uv run kb ingest book-cube-archive --archive data/raw/book-cube/export
uv run kb index rebuild --target all
```

## Import Zip

```bash
uv run kb ingest book-cube-archive --archive data/raw/book-cube/export.zip
uv run kb index rebuild --target all
```

## Query

```bash
uv run kb search text "known phrase from the archive"
uv run kb graph neighbors --topic books
uv run kb search hybrid "книжные заметки"
```

## Tests

```bash
uv run --extra test pytest tests/unit/test_book_cube_archive.py
KB_RUN_INTEGRATION=1 uv run --extra test pytest tests/integration/test_book_cube_archive_pipeline.py
```
