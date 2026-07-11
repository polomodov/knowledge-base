# Архитектура

Документ описывает целевую архитектуру `knowledge-base`. Это не финальная схема реализации, а рабочий контур, который помогает добавлять источники и функции без смешивания ответственности.

## Целевой поток

```text
sources -> ingest -> normalize -> store -> index/search -> visualize/write
```

- **Sources** - внешние или локальные источники личных материалов: "Книжный куб", Medium, заметки, документы, архивы ссылок.
- **Ingest** - source adapters, которые получают данные из конкретного источника и сохраняют raw-снимок или читаемый экспорт.
- **Normalize** - преобразование сырого материала в единый формат: текст, метаданные, даты, ссылки, теги, цитаты.
- **Store** - долговременное хранение raw-данных, обработанных документов, индексов и generated outputs в разных зонах.
- **Index/Search** - полнотекстовый (BM25) и семантический (ANN) поиск, graph-aware hybrid-ранжирование, community detection и local/global GraphRAG-поиск поверх графа знаний (GraphRAG-эпик GR-0…GR-6 завершён — см. [graphrag-plan.md](graphrag-plan.md)).
- **Visualize/Write** - графы, карты тем, исследовательские панели и writing workflows для подготовки постов, статей и книг.

## Подсистемы

### Source adapters

Каждый адаптер отвечает только за один источник или один тип экспорта. Он должен описывать:

- вход: API, экспорт, HTML, Markdown, JSON, CSV или локальная папка;
- выход: raw-снимок и набор нормализуемых элементов;
- ограничения: rate limits, приватность, неполные метаданные, ручные шаги;
- provenance: какие поля позволяют восстановить происхождение материала.

### Storage

Хранилище должно разделять:

- `raw` - исходные данные без потери контекста;
- `processed` - нормализованные документы и метаданные;
- `generated` - производные материалы: summaries, черновики, отчеты, LLM outputs.

Это разделение важно, чтобы можно было пересобрать базу после изменения нормализации или индексации.

**Как это реализовано в v1.** ArangoDB сейчас является единой зоной хранения: raw snapshot (полный payload или manifest), нормализованные documents/chunks и derived индексы живут в одной базе. Разделение `data/raw` / `data/processed` / `data/generated` в репозитории - это соглашение для on-disk артефактов: `data/raw/` хранит исходные экспорты/снимки, которые вы передаёте адаптерам, а `data/generated/` - выходы `kb export`. `data/processed/` пока не используется (нормализованные данные живут в ArangoDB). При live-ingest по URL сырьё сохраняется только внутри базы (`raw_snapshots.payload`), поэтому для воспроизводимости «пересобрать processed из raw» держите исходные снимки в `data/raw/` и импортируйте их через `--input`/`--archive`.

### Search and embeddings

Индекс поиска должен строиться поверх `processed`, а не напрямую поверх `raw`. Эмбеддинги и RAG-контекст должны сохранять ссылки на документы и provenance, чтобы любой найденный фрагмент можно было проверить по исходному источнику.

Первый production-like pipeline проектируется вокруг ArangoDB: documents/chunks, graph edges, ArangoSearch full-text и vector indexes живут в одном multi-model ядре. Это снижает количество движущихся частей в v1, но сохраняет явные границы storage/search/vector/graph, чтобы позже вынести отдельный движок при bottleneck.

Эмбеддинги подключаемы через `EmbeddingProvider` (`embedding.provider`): дефолт `hash` — детерминированный, offline, без зависимостей (ingest и запрос используют один провайдер, поэтому векторы в одном пространстве). Провайдер `local` даёт реальные семантические эмбеддинги через `sentence-transformers` (ставится вручную, вне locked-зависимостей). `embedding.dimension` — единый источник размерности для vector index. См. [docs/graphrag-plan.md](graphrag-plan.md) (GR-2).

Текущий v1 fixture slice реализует этот контур через Python CLI `kb`:

- `kb platform bootstrap` создает коллекции, edge collections, ArangoSearch View, graph definition и vector index.
- `kb ingest fixture` загружает безопасный synthetic fixture и создает source/raw/document/chunk/topic/author/work records.
- `kb ingest tellmeabout-tech` загружает публичные посты из RSS/Atom или локального snapshot, создавая source/raw/document/chunk/topic/author records.
- `kb ingest medium-export` загружает локальный Medium account export directory или `.zip`, создает raw manifest snapshot и импортирует опубликованные `posts/*.html` как documents/chunks/author records.
- `kb ingest book-cube` загружает публичные посты Telegram-канала из HTML/JSON snapshot, создавая source/raw/document/chunk/topic records.
- `kb ingest book-cube-archive` загружает полный владельческий Telegram Desktop JSON archive из directory или `.zip`, создавая documents/chunks/topics и сохраняя media/file attachments только как local raw references.
- `kb index rebuild --target all` идемпотентно проверяет derived search/vector/graph слой; `--target related` отдельно строит `item_related_to_item` — взвешенные similarity-рёбра между похожими чанками из разных документов (GR-3), превращая дерево провенанса в граф знаний. Качество связей максимально с реальной моделью эмбеддингов (`embedding.provider = local`). `--target communities` кластеризует этот similarity-граф Louvain-оптимизацией модулярности (чистый Python, без зависимостей; параметр гранулярности `[community] resolution`) в узлы `communities` с экстрактивными summaries (GR-4) — основа для global-search GraphRAG.
- `kb search text`, `kb search semantic`, `kb graph neighbors`, `kb search hybrid`, `kb search local` и `kb search global` возвращают результаты с provenance; retrieval-команды поддерживают optional source filter для исследования одного источника.
  - `kb search hybrid` сливает полнотекст (BM25) и вектор и **вкладывает графовый сигнал в ранжирование**: `score_components.graph_boost` — ограниченный буст за общие сущности (GR-1) и similarity-рёбра `item_related_to_item` (GR-3b). `graph_boost = null` только если графовый слой деградировал. Если relevance-гейт оставил пустые слоты, `hybrid` дозаполняет их graph-only соседями топ-хитов (GR-3c, `graph_expanded: true`) — они дописываются после реальных хитов и не могут их перевесить.
  - `kb search local` (GR-5) собирает локальный подграф вокруг сильнейших документов запроса: связывающие сущности, similarity-соседи и сообщества. `kb search global` (GR-5) отвечает на уровне корпуса поверх community summaries (GR-4): сопоставляет retrieval-хиты сообществам и возвращает топ сообществ с summary и документами-цитатами. Оба контекста экстрактивные и цитируемые (без LLM). Полный трекинг GraphRAG-подсистемы — [docs/graphrag-plan.md](graphrag-plan.md).
- `kb export jsonl` пишет generated exports в gitignored data zone.

### Visualization

Визуализация должна помогать исследовать связи между источниками, темами, книгами, авторами, датами и собственными текстами. Она не должна становиться источником истины: визуальные представления пересобираются из нормализованных данных.

### Writing assistant

Writing/research workflow использует базу знаний как контекст для письма, но отделяет черновики и generated outputs от исходных материалов. Любой фрагмент, использованный в публикации или исследовании, должен иметь ссылку на первичный источник.

### Architecture Decision Records

Architecture Decision Records живут в [docs/adr](adr/README.md). Они фиксируют значимые решения о данных, privacy, provenance, storage, search/RAG, визуализации и workflow. ADR являются docs-only артефактами и не входят в raw, processed или generated data zones.

Локальный tooling:

- `npm run adr:new` создает следующий нумерованный ADR по шаблону.
- `npm run generate:adr-index` обновляет индекс в `docs/adr/README.md`.
- `npm run check:adr` проверяет метаданные, обязательные RU/EN-секции, связи supersession и свежесть индекса.

### Spec-driven development

Feature workflow строится через GitHub Spec Kit: `.specify/` хранит upstream templates/scripts/memory, `.agents/skills/` хранит Codex skills, а будущие `specs/` содержат спецификации, планы и задачи фичей. Эти документы являются project artifacts и не входят в data zones.

## Минимальные сущности

- **Source** - описание происхождения данных: канал, блог, локальный архив, API или экспорт.
- **Document/Item** - импортированная единица знания: пост, заметка, цитата, статья, фрагмент книги или ссылка.
- **Metadata** - дата, автор, язык, теги, URL, тип материала, статус обработки.
- **Citation/Provenance** - путь назад к источнику: origin, source URL, import timestamp, original identifier, context.
- **Topic/Tag** - тематическая разметка, добавленная вручную или автоматически.

## Принципы реализации

- Начинать с одного источника и сквозного вертикального среза.
- Предпочитать простые форматы и воспроизводимые команды до появления реальной необходимости в сложной инфраструктуре.
- Не смешивать импорт, нормализацию, индексацию и визуализацию в одном модуле.
- Сохранять возможность пересобрать processed-данные и индексы из raw-источников.
- Документировать изменения структуры данных вместе с кодом.

## Связанные документы

- [README.md](../README.md)
- [Roadmap](roadmap.md)
- [ADR decision log](adr/README.md)
- [Production Knowledge Pipeline spec](../specs/001-production-knowledge-pipeline/spec.md)
- [Tell Me About Tech Source spec](../specs/002-tellmeabout-tech-source/spec.md)
- [Book Cube Telegram Source spec](../specs/003-book-cube-telegram-source/spec.md)
- [Book Cube Owner Archive Import spec](../specs/004-book-cube-owner-archive-import/spec.md)
- [Medium Export Source spec](../specs/005-medium-export-source/spec.md)
