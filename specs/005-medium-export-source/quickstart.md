# Quickstart: Medium Export Source

Скопируйте или оставьте Medium export в gitignored raw storage:

```bash
mkdir -p data/raw/medium/apolomodov
cp -R ~/Downloads/medium-export-2026-06-06 data/raw/medium/apolomodov/
```

Импортируйте опубликованные посты и перестройте индексы:

```bash
uv run kb ingest medium-export --archive data/raw/medium/apolomodov/medium-export-2026-06-06
uv run kb index rebuild --target all
```

Поиск по Medium-статьям лучше запускать с явным source filter:

```bash
uv run kb search text "known phrase from Medium" --source medium-export
uv run kb search semantic "agent platform internal developer portal" --source medium-export
uv run kb search hybrid "architecture writing research" --source medium-export
uv run kb graph neighbors --author alexander-polomodov --source medium-export --documents-only
```

Явно импортируйте drafts только если они нужны как документы:

```bash
uv run kb ingest medium-export --archive data/raw/medium/apolomodov/medium-export-2026-06-06 --include-drafts
```

Запустите проверки:

```bash
uv run --extra test pytest tests/unit/test_medium_export_source.py
KB_RUN_INTEGRATION=1 uv run --extra test pytest tests/integration/test_medium_export_pipeline.py
```
