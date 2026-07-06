# CLI Contract: Medium Export Source

## `kb ingest medium-export --archive PATH`

Импортирует опубликованные `posts/*.html` из Medium account export directory или zip.

Успешный ответ:

```json
{
  "status": "ok",
  "source_key": "medium-export",
  "import_run_key": "import-medium-export-...",
  "archive": {
    "kind": "directory",
    "ref": "data/raw/medium/apolomodov/medium-export-2026-06-06",
    "manifest_sha256": "...",
    "total_files": 372,
    "total_size_bytes": 9750000,
    "root": "data/raw/medium/apolomodov/medium-export-2026-06-06"
  },
  "include_drafts": false,
  "created": {
    "sources": 1,
    "raw_snapshots": 1,
    "documents": 340,
    "chunks": 900,
    "topics": 0,
    "authors": 1,
    "works": 0,
    "edges": 1240
  },
  "deduplicated": {
    "documents": 0,
    "chunks": 0
  },
  "skipped": [
    {
      "guid": "fed456fed456",
      "reason": "draft_excluded"
    }
  ]
}
```

## `kb ingest medium-export --archive PATH --include-drafts`

Импортирует опубликованные посты и drafts. Draft documents используют `status="draft"` и могут иметь `published_at=null`.

## Ошибка

```json
{
  "status": "error",
  "error": "archive_not_readable",
  "source_key": "medium-export",
  "archive": "data/raw/medium/apolomodov/missing",
  "hint": "Copy Medium export under data/raw/medium/ and rerun with --archive.",
  "reason": "Archive path does not exist"
}
```

Допустимые значения `error`:

- `archive_not_readable`
- `posts_not_found`
- `invalid_medium_export`

## Retrieval examples

Ограничить поиск импортированными Medium-статьями:

```bash
kb search text "Agent-first IDP" --source medium-export
kb search semantic "agent platform internal developer portal" --source medium-export
kb search hybrid "developer productivity AI code assistants" --source medium-export
```

Вернуть distinct Medium documents, связанные с автором:

```bash
kb graph neighbors --author alexander-polomodov --source medium-export --documents-only
```
