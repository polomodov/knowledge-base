# knowledge-base

Персональная база знаний для сбора, нормализации, поиска и переиспользования материалов из собственных источников: канала "Книжный куб", блога на Medium и будущих архивов заметок, публикаций или исследовательских материалов.

Проект содержит исполнимый вертикальный срез с завершёнными GraphRAG- и visualization-слоями: локальный ArangoDB runtime, безопасный fixture ingest, source adapters для публичного блога `tellmeabout.tech`, Medium account export и Telegram-канала "Книжный куб" (включая владельческий Telegram Desktop archive import), schema/index bootstrap, полнотекстовый (BM25) и семантический (ANN) поиск, подключаемые эмбеддинги (детерминированный hash и локальная модель), граф знаний с similarity-рёбрами, **граф-осведомлённый hybrid retrieval**, community detection (Louvain), **local/global GraphRAG-поиск**, локальный read-only MCP server, стандартный graph export и самодостаточная offline-визуализация.

## Зачем

Цель проекта - превратить разрозненные личные материалы в воспроизводимую knowledge database, которую можно использовать для:

- поиска идей, цитат, заметок и связей между темами;
- подготовки постов, эссе, исследований и книг;
- анализа собственной истории чтения, письма и интересов;
- визуализации тем, источников, авторов, книг и смысловых кластеров;
- работы с RAG/LLM-инструментами поверх собственной базы знаний.

## Планируемые источники

- **"Книжный куб"** - Telegram-канал с книжными заметками, цитатами, размышлениями и рекомендациями; поддерживает public snapshots и полный владельческий Telegram Desktop JSON archive.
- **tellmeabout.tech** - публичный блог на Medium/custom domain; первый реальный source adapter.
- **Medium/export** - локальные HTML-экспорты собственных Medium-статей из account export; v1 импортирует опубликованные `posts/*.html`, drafts остаются raw-only по умолчанию.
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
- Source adapter `tellmeabout-tech` для публичных постов из RSS/Atom или локального snapshot/export.
- Source adapter `medium-export` для локального Medium account export directory или `.zip`.
- Source adapter `book-cube` для публичных постов Telegram-канала из `t.me/s` HTML snapshot или одиночного Telegram Desktop JSON export.
- Source adapter `book-cube-archive` для полного владельческого Telegram Desktop JSON archive из directory или `.zip` с `result.json`; media binaries остаются локальными raw references.
- Ingest fixture с provenance edges: source, raw snapshot, document, chunk, topic, author, work.
- Подключаемые эмбеддинги: детерминированный `hash` (dim 8, zero-dependency, по умолчанию) и `local` (sentence-transformers, напр. `all-mpnet-base-v2`, 768d); переключение провайдера/модели без re-ingest через `kb index rebuild --target embeddings`.
- Derived-индексы: `--target related` (similarity-рёбра `item_related_to_item` через ANN) и `--target communities` (Louvain community detection + экстрактивные summaries).
- Retrieval-команды: `kb search text` (BM25), `kb search semantic` (ANN + relevance-гейт), `kb search hybrid` (BM25 + вектор + **graph_boost**, с расширением кандидатов графом), `kb graph neighbors` (обход графа знаний).
- GraphRAG-поиск: `kb search local` (подграф вокруг релевантных сущностей) и `kb search global` (retrieval-conditioned обзор community summaries из bounded candidate pool) — экстрактивный цитируемый контекст с провенансом.
- Read-only MCP server `kb-mcp` для локальных агентов и других проектов.
- `kb export jsonl` для generated exports в gitignored data zone.
- `kb export graph` для полного doc-level графа и topic co-occurrence в node-link JSON/GraphML.
- `kb viz build` для самодостаточного offline HTML: карта сообществ/топиков, таймлайн и ego-граф документов без CDN, сервера или npm runtime.
- Unit и integration tests (включая проверку на живой ArangoDB), CI (ruff + mypy + pytest) и SonarCloud.

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
uv run kb index rebuild --target related        # similarity-рёбра item_related_to_item
uv run kb index rebuild --target communities    # community detection (Louvain) + summaries
uv run kb search text "systems thinking"
uv run kb search semantic "ideas across books"
uv run kb graph neighbors --topic systems-thinking
uv run kb graph neighbors --author fixture-author
uv run kb search hybrid "systems thinking writing workflow"
uv run kb search local "systems thinking"       # GraphRAG local: подграф вокруг хитов
uv run kb search global "ideas across books"     # GraphRAG global: поверх community summaries
uv run kb export jsonl --output data/generated/exports/fixture.jsonl
```

Fixture имеет служебный `status=fixture`, поэтому published-only визуализация трактует его как no-data smoke. На реальном опубликованном корпусе:

```bash
uv run kb export graph --format graphml --output data/generated/graph/knowledge-base.graphml
uv run kb viz build                              # data/generated/viz/knowledge-base.html
```

### Семантический поиск и GraphRAG на реальной модели

Fixture использует детерминированный `hash`-провайдер (dim 8), достаточный для смоука. Для осмысленного semantic/GraphRAG-поиска на реальном корпусе включите локальную модель эмбеддингов — задайте `[embedding] provider = "local"` в конфиге (`--config`) или через env:

```bash
export KB_EMBEDDING_PROVIDER=local
export KB_EMBEDDING_MODEL=sentence-transformers/all-mpnet-base-v2
export KB_EMBEDDING_DIMENSION=768
```

После смены провайдера/модели переэмбеддите корпус и пересоберите производный граф (re-ingest не нужен):

```bash
uv run kb index rebuild --target embeddings     # пересчитать векторы + vector index под 768d
uv run kb index rebuild --target related        # similarity-рёбра на новых эмбеддингах
uv run kb index rebuild --target communities    # сообщества на обновлённом графе
```

Затем — семантический и GraphRAG-поиск с relevance-гейтом:

```bash
uv run kb search semantic "distributed systems consistency" --min-similarity 0.35
uv run kb search hybrid   "engineering management and leading teams" --limit 5
uv run kb search local    "distributed database consensus"
uv run kb search global   "engineering leadership" --communities 5
```

> Важно: если стор содержит 768d-эмбеддинги, а провайдер остался `hash` (dim 8), semantic/hybrid не найдут векторных совпадений (несовпадение размерности/модели). Держите провайдер согласованным со стором.

### Проверка работы (smoke-test)

Быстрый гейт качества (то же, что гоняет CI):

```bash
uv run --extra dev ruff check src tests
uv run --extra dev ruff format --check src tests
uv run --extra dev mypy
uv run --extra dev pytest tests/unit -q            # unit, без БД
KB_RUN_INTEGRATION=1 uv run --extra dev pytest -q  # полный прогон с живым ArangoDB
node scripts/check-viz-template.mjs                # offline JS/CSP/XSS-гейт
```

Проверка живого окружения и корпуса:

```bash
uv run kb platform health          # status: ok, exit code 0 = готово
uv run kb search hybrid "distributed systems and databases" --limit 4
```

Признаки, что база работает: `platform health` → `status: ok`; результаты поиска **по теме** запроса с непустым `provenance`; в `hybrid` поле `score_components.graph_boost` — число в `[0, 0.5]` (не `null`), а `score` монотонно убывает; `search local`/`global` возвращают связывающие сущности / сообщества с summary.

Прогнать первый реальный source adapter на локальном snapshot:

```bash
uv run kb ingest tellmeabout-tech --input data/raw/tellmeabout-tech/feed.xml
uv run kb index rebuild --target all
uv run kb search text "known phrase from the blog"
uv run kb graph neighbors --topic product-thinking
uv run kb search hybrid "technology writing systems"
```

Live feed можно попробовать так:

```bash
uv run kb ingest tellmeabout-tech --feed-url https://tellmeabout.tech/feed
```

Если сайт или Medium блокирует автоматический доступ, сохраните RSS/Medium export в `data/raw/tellmeabout-tech/` и используйте `--input`. Эта зона игнорируется git.

Прогнать импорт собственного Medium account export:

```bash
uv run kb ingest medium-export --archive data/raw/medium/apolomodov/medium-export-2026-06-06
uv run kb ingest medium-export --archive data/raw/medium/apolomodov/medium-export.zip
uv run kb index rebuild --target all
uv run kb search text "known phrase from Medium" --source medium-export
uv run kb graph neighbors --author alexander-polomodov --source medium-export --documents-only
uv run kb search hybrid "architecture writing research" --source medium-export
```

Medium export должен оставаться в `data/raw/medium/`, который игнорируется git. Адаптер v1 нормализует только опубликованные `posts/*.html`; `profile`, `sessions`, `ips`, `notes`, `bookmarks`, `claps`, following lists и drafts по умолчанию остаются только raw provenance. Если нужно явно импортировать черновики, используйте `--include-drafts`. Это ingest-only opt-in: однажды импортированный draft доступен обычным CLI/MCP search и JSONL export без отдельного query-флага; для приватных drafts используйте отдельную БД или не импортируйте их (см. [ADR 0005](docs/adr/0005-define-source-provenance-and-private-archive-boundaries.md)).

Прогнать второй реальный source adapter на локальном snapshot:

```bash
uv run kb ingest book-cube --input data/raw/book-cube/channel.html
uv run kb ingest book-cube --input data/raw/book-cube/result.json
uv run kb index rebuild --target all
uv run kb search text "known phrase from the channel"
uv run kb graph neighbors --topic books
uv run kb search hybrid "книжные заметки"
```

Live public preview можно попробовать так:

```bash
uv run kb ingest book-cube --url https://t.me/s/book_cube
```

Если Telegram блокирует или live URL таймаутится, сохраните public channel HTML snapshot или Telegram Desktop JSON export в `data/raw/book-cube/` и используйте `--input`.

Прогнать полный владельческий архив "Книжного куба":

```bash
uv run kb ingest book-cube-archive --archive data/raw/book-cube/export
uv run kb ingest book-cube-archive --archive data/raw/book-cube/export.zip
uv run kb index rebuild --target all
uv run kb search text "known phrase from the archive"
uv run kb graph neighbors --topic books
uv run kb search hybrid "книжные заметки из архива"
```

Для полного архива используйте Telegram Desktop export в режиме **Machine-readable JSON**. Реальный архив и вложенные media/files должны оставаться в `data/raw/book-cube/`, который игнорируется git; в репозитории хранятся только synthetic fixtures.

Подключить базу знаний к MCP-клиенту:

```bash
cp config/pipeline.example.toml config/pipeline.local.toml
# Настройте provider/model/dimension как у уже построенного корпуса.
# Только для embedding.provider = "local" (тяжёлая зависимость намеренно вне lock-файла):
uv pip install sentence-transformers
uv run --extra mcp kb-mcp --config config/pipeline.local.toml
```

Пример локальной stdio-конфигурации для MCP-клиента:

```json
{
  "mcpServers": {
    "knowledge-base": {
      "command": "uv",
      "args": [
        "run",
        "--extra",
        "mcp",
        "kb-mcp",
        "--config",
        "/absolute/path/to/knowledge-base/config/pipeline.local.toml"
      ],
      "cwd": "/absolute/path/to/knowledge-base"
    }
  }
}
```

MCP v1 работает локально через stdio и открывает только read-only tools/resources/prompts: `kb_search`, `kb_get_document`, `kb_graph_neighbors`, `kb_list_sources`, `kb_health`, `kb://sources`, `kb://documents/{document_key}` и `research_knowledge_base`. `kb_search` поддерживает `text`, `semantic`, `hybrid`, `local` и `global`; embedding-backed режимы используют provider/model/dimension и `retrieval.min_similarity` из того же конфига, что CLI и индекс корпуса. `pipeline.example.toml` настроен на fixture/hash-эмбеддинги; для реального корпуса локальный конфиг обязан совпадать с моделью, которой выполнен `--target embeddings`, а provider `local` требует установленный `sentence-transformers`. Stdio/read-only не является per-client access control: любой local process с DB credentials может читать normalized corpus (trust boundary — [ADR 0006](docs/adr/0006-define-the-local-security-and-privacy-trust-boundary.md)).

Проверки:

```bash
uv run --extra dev pytest tests/unit
uv run --extra dev --extra mcp pytest tests/unit
KB_RUN_INTEGRATION=1 uv run --extra dev --extra mcp pytest
uv run --extra dev ruff check src tests
uv run --extra dev mypy
npm run check:adr
```

Integration-тесты работают против выделенной БД `knowledge_base_integration_test` (сбрасывается в начале каждого прогона), чтобы не засевать тест-данные в ваш реальный корпус `knowledge_base`. Переопределить — задать `KB_ARANGO_DATABASE` явно.

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
│   ├── 001-production-knowledge-pipeline/
│   ├── 002-tellmeabout-tech-source/
│   ├── 003-book-cube-telegram-source/
│   ├── 004-book-cube-owner-archive-import/
│   ├── 005-medium-export-source/
│   └── 006-knowledge-base-mcp-server/
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
- [docs/architecture.md](docs/architecture.md) - целевая архитектура, ключевые сущности и диаграммы (системный поток, модель данных графа).
- [docs/graphrag-plan.md](docs/graphrag-plan.md) - GraphRAG-эпик (GR-0…GR-6): статус, «как работает база знаний сейчас» и диаграммы конвейеров retrieval/GraphRAG.
- [docs/visualization.md](docs/visualization.md) - команды, схемы JSON/GraphML/HTML, деградации и контрольные размеры v4.
- [docs/viz-smoke-checklist.md](docs/viz-smoke-checklist.md) - ручная offline-проверка трёх визуальных видов.
- [docs/roadmap.md](docs/roadmap.md) - этапы развития проекта.
- [docs/adr/README.md](docs/adr/README.md) - журнал архитектурных решений и ADR-процесс.
- [specs/001-production-knowledge-pipeline/spec.md](specs/001-production-knowledge-pipeline/spec.md) - Spec Kit feature для ArangoDB-centered production pipeline.
- [specs/002-tellmeabout-tech-source/spec.md](specs/002-tellmeabout-tech-source/spec.md) - Spec Kit feature для первого реального источника.
- [specs/003-book-cube-telegram-source/spec.md](specs/003-book-cube-telegram-source/spec.md) - Spec Kit feature для Telegram-канала "Книжный куб".
- [specs/004-book-cube-owner-archive-import/spec.md](specs/004-book-cube-owner-archive-import/spec.md) - Spec Kit feature для полного владельческого Telegram archive import.
- [specs/005-medium-export-source/spec.md](specs/005-medium-export-source/spec.md) - Spec Kit feature для Medium account export import.
- [specs/006-knowledge-base-mcp-server/spec.md](specs/006-knowledge-base-mcp-server/spec.md) - Spec Kit feature для read-only MCP server.
- [specs/007-writer-research-workflow/spec.md](specs/007-writer-research-workflow/spec.md) - проектируемая Spec Kit feature V5 для provenance-first research dossier, citations и file round-trip с writing-agent.

## Spec-Driven Development

Проект использует scoped hybrid workflow из [ADR 0009](docs/adr/0009-scope-spec-kit-and-plan-tracker-workflows.md). [GitHub Spec Kit](https://github.com/github/spec-kit) с Codex integration остается default для новых пользовательских фич, feature/API/CLI-контрактов, source adapters и import workflows. Инструменты Spec Kit живут в `.specify/`, а Codex skills - в `.agents/skills/`.

Проверить установленный CLI и integration:

```bash
specify version
specify integration status
```

Базовый workflow для таких фич:

```text
$speckit-constitution -> $speckit-specify -> $speckit-plan -> $speckit-tasks -> $speckit-implement
```

Для ограниченных сквозных remediation-, audit-, research-, architecture- и infrastructure-эпиков с заранее зафиксированным scope допустим docs plan tracker. Причина выбора tracker вместо Spec Kit фиксируется в плане или связанном ADR; один tracker служит каноническим источником статуса и обязан содержать решения, зависимости, критерии приемки и валидацию. Значимые архитектурные решения всё равно оформляются ADR. Простые исправления и локальные рефакторинги без изменения контракта не требуют полного Spec Kit.

Feature specs по умолчанию пишутся на русском с кратким English summary. Specs, plans и tasks являются project artifacts и не должны смешиваться с `data/raw`, `data/processed` или `data/generated`.

Первый крупный feature design: [Production Knowledge Pipeline](specs/001-production-knowledge-pipeline/spec.md). Он проектирует ArangoDB как multi-model ядро для documents, graph, full-text search, vector search и hybrid retrieval.

Первый реальный source adapter: [Tell Me About Tech Source](specs/002-tellmeabout-tech-source/spec.md). Он импортирует публичные посты из RSS/Atom или локального snapshot, не пытаясь обходить Cloudflare/Medium protections.

Второй source adapter: [Book Cube Telegram Source](specs/003-book-cube-telegram-source/spec.md). Он импортирует публичные посты из HTML snapshot `t.me/s/book_cube` или одиночного Telegram Desktop JSON export, не пытаясь обходить Telegram protections.

Расширение второго source adapter: [Book Cube Owner Archive Import](specs/004-book-cube-owner-archive-import/spec.md). Оно импортирует полный владельческий Telegram Desktop JSON archive из directory или `.zip`, сохраняет attachment references как metadata и не коммитит реальные raw/media данные.

Третий реальный source adapter: [Medium Export Source](specs/005-medium-export-source/spec.md). Он импортирует локальный Medium account export directory или `.zip`, строит raw manifest snapshot и нормализует опубликованные `posts/*.html` с Medium post id, canonical URL, author, dates and provenance.

Локальный read-only MCP-интерфейс: [Knowledge Base MCP Server](specs/006-knowledge-base-mcp-server/spec.md). Он открывает search, document expansion, graph neighbors, source inventory, health, resources and research prompt для других проектов без ingest/index/export mutations.

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
- **v2** - импорт первых реальных источников `tellmeabout.tech` и "Книжный куб", включая полный владельческий archive import.
- **v3** ✅ - расширенный GraphRAG (граф-осведомлённый hybrid, community detection, local/global search), семантические эмбеддинги, качество retrieval и локальный read-only MCP server — завершён (GR-0…GR-6, см. [docs/graphrag-plan.md](docs/graphrag-plan.md)).
- **v4** ✅ - node-link JSON/GraphML + самодостаточный offline HTML с картой сообществ/топиков, таймлайном и ego-графом документов (см. [docs/visualization.md](docs/visualization.md) и [ADR 0008](docs/adr/0008-adopt-offline-visualization-and-graph-export.md)).
- **v5** 🟡 - writer/research workflow находится в design phase: Feature 007 проектирует immutable research dossier, chunk citations и проверяемый file round-trip с внешним writing-agent; runtime ещё не реализован.

Подробнее: [docs/roadmap.md](docs/roadmap.md).
