# Tasks: Medium Export Source

- [x] Скопировать локальный Medium export в gitignored `data/raw/medium/apolomodov/`.
- [x] Добавить reader для Medium export directory/zip с manifest hash.
- [x] Добавить Medium HTML parser для опубликованных постов и optional drafts.
- [x] Добавить `kb ingest medium-export --archive PATH [--include-drafts]`.
- [x] Upsert source, raw snapshot, import run, documents, chunks and author edges.
- [x] Расширить retrieval provenance optional полем `medium_post`.
- [x] Добавить exact source filter в text/semantic/hybrid/graph retrieval API и CLI.
- [x] Добавить graph `--documents-only` режим для distinct document выдачи.
- [x] Добавить synthetic fixtures and unit tests.
- [x] Добавить integration test for ingest/search/graph/hybrid.
- [x] Обновить README, architecture, roadmap и Spec Kit docs.
