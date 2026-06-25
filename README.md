# knowledge-base

Персональная база знаний для сбора, нормализации, поиска и переиспользования материалов из собственных источников: канала "Книжный куб", блога на Medium и будущих архивов заметок, публикаций или исследовательских материалов.

Проект находится на ранней стадии, но уже содержит первый исполнимый вертикальный срез: локальный ArangoDB runtime, безопасный fixture ingest, schema/index bootstrap, full-text search, deterministic embeddings, graph traversal, hybrid retrieval и JSONL export. Реальные персональные источники пока не подключены.

## Зачем

Цель проекта - превратить разрозненные личные материалы в воспроизводимую knowledge database, которую можно использовать для:

- поиска идей, цитат, заметок и связей между темами;
- подготовки постов, эссе, исследований и книг;
- анализа собственной истории чтения, письма и интересов;
- визуализации тем, источников, авторов, книг и смысловых кластеров;
- работы с RAG/LLM-инструментами поверх собственной базы знаний.

## Планируемые источники

- **"Книжный куб"** - персональный канал с книжными заметками, цитатами, размышлениями и рекомендациями.
- **Medium** - личный блог с опубликованными текстами и связанными черновыми идеями.
- **Другие источники** - локальные заметки, экспорт из read-it-later сервисов, документы, подборки ссылок, исследовательские архивы.

Каждый источник должен сохранять provenance: откуда пришел материал, когда он был получен, какой у него исходный URL или канал, и в каком контексте он был создан.

## Жизненный цикл данных

Ожидаемый поток обработки:

1. **Ingest** - загрузить или импортировать данные из исходного источника.
2. **Normalize** - привести материалы к общему представлению: текст, метаданные, даты, теги, ссылки, цитаты.
3. **Index/Search** - построить полнотекстовый поиск, эмбеддинги и тематические индексы.
4. **Visualize/Write** - исследовать базу визуально и использовать ее при письме, ресерче и генерации черновиков.

Raw-данные, нормализованные данные и generated outputs должны храниться отдельно. Generated outputs не являются источником истины и должны ссылаться на использованные материалы.

## Что уже есть

- Python package `knowledge_base` и CLI `kb`.
- ArangoDB Compose runtime с `--vector-index`.
- Идемпотентный bootstrap коллекций, edge collections, ArangoSearch View, graph definition и vector index.
- Safe synthetic fixture без персональных данных.
- Ingest fixture с provenance edges: source, raw snapshot, document, chunk, topic, author, work.
- Retrieval-команды: `kb search text`, `kb search semantic`, `kb graph neighbors`, `kb search hybrid`.
- `kb export jsonl` для generated exports в gitignored data zone.
- Unit и integration tests, включая проверку на живой ArangoDB.

## Быстрый старт

Установить зависимости и поднять runtime:

```bash
uv run kb --help
brew install colima docker docker-compose
colima start --cpu 4 --memory 8 --disk 60
uv run kb platform up
uv run kb platform bootstrap
uv run kb platform health
```

Прогнать fixture pipeline:

```bash
uv run kb ingest fixture
uv run kb index rebuild --target all
uv run kb search text "systems thinking"
uv run kb search semantic "ideas across books"
uv run kb graph neighbors --topic systems-thinking
uv run kb graph neighbors --author fixture-author
uv run kb search hybrid "systems thinking writing workflow"
uv run kb export jsonl --output data/generated/exports/fixture.jsonl
```

Проверки:

```bash
uv run --extra test pytest tests/unit
KB_RUN_INTEGRATION=1 uv run --extra test pytest
npm run check:adr
```

## Структура

Текущая структура:

```text
.
├── README.md
├── AGENTS.md
├── pyproject.toml
├── package.json
├── compose/
│   └── arangodb.compose.yml
├── config/
│   ├── arangodb.env.example
│   └── pipeline.example.toml
├── docs/
│   ├── architecture.md
│   ├── roadmap.md
│   └── adr/
├── specs/
│   └── 001-production-knowledge-pipeline/
├── scripts/
│   └── ...
├── data/
│   ├── raw/
│   ├── processed/
│   └── generated/
├── src/
│   └── knowledge_base/
└── tests/
    ├── fixtures/
    ├── integration/
    └── unit/
```

`data/raw/`, `data/processed/` и `data/generated/` игнорируются git. В репозитории можно хранить только безопасные fixtures, схемы, specs и документацию.

## Документация

- [AGENTS.md](AGENTS.md) - правила для Codex и других агентов, работающих с репозиторием.
- [docs/architecture.md](docs/architecture.md) - целевая архитектура и ключевые сущности.
- [docs/roadmap.md](docs/roadmap.md) - этапы развития проекта.
- [docs/adr/README.md](docs/adr/README.md) - журнал архитектурных решений и ADR-процесс.
- [specs/001-production-knowledge-pipeline/spec.md](specs/001-production-knowledge-pipeline/spec.md) - Spec Kit feature для ArangoDB-centered production pipeline.

## Spec-Driven Development

Проект использует [GitHub Spec Kit](https://github.com/github/spec-kit) с Codex integration для spec-driven development. Инструменты Spec Kit живут в `.specify/`, а Codex skills - в `.agents/skills/`.

Проверить установленный CLI и integration:

```bash
specify version
specify integration status
```

Базовый workflow для будущих фич:

```text
$speckit-constitution -> $speckit-specify -> $speckit-plan -> $speckit-tasks -> $speckit-implement
```

Feature specs по умолчанию пишутся на русском с кратким English summary. Specs, plans и tasks являются project artifacts и не должны смешиваться с `data/raw`, `data/processed` или `data/generated`.

Первый крупный feature design: [Production Knowledge Pipeline](specs/001-production-knowledge-pipeline/spec.md). Он проектирует ArangoDB как multi-model ядро для documents, graph, full-text search, vector search и hybrid retrieval.

## Architecture Decisions

Architecture Decision Records живут в [docs/adr](docs/adr). Это docs-only артефакты: они объясняют важные решения, но не являются исходными данными, обработанными данными или generated outputs.

Создать новый ADR:

```bash
npm run adr:new -- --title-ru "Короткий русский заголовок" --title-en "Short English title"
```

Обновить и проверить индекс решений:

```bash
npm run generate:adr-index
npm run check:adr
```

## Roadmap

- **v0** - стартовая документация, принципы, архитектурный контур.
- **v1** - production-like ArangoDB fixture pipeline с provenance, search, vector, graph и hybrid retrieval.
- **v2** - импорт первого реального источника и нормализация.
- **v3** - расширенный GraphRAG, embeddings и качество retrieval.
- **v4** - визуализация тем, источников и связей.
- **v5** - writer/research workflow поверх базы знаний.

Подробнее: [docs/roadmap.md](docs/roadmap.md).
