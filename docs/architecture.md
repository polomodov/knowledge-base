# Архитектура

Документ описывает целевую архитектуру `knowledge-base`. Это не финальная схема реализации, а рабочий контур, который помогает добавлять источники и функции без смешивания ответственности.

## Целевой поток

```mermaid
flowchart LR
    S["Источники<br/>Книжный куб · Medium<br/>tellmeabout.tech · fixture"]
    S --> I["Ingest<br/>адаптеры источников"]
    I --> DB
    subgraph DB["ArangoDB — мультимодель"]
        direction TB
        D["documents / chunks<br/>+ 768d эмбеддинги"]
        G["граф знаний<br/>knowledge_graph"]
        X["индексы<br/>BM25 view · vector ANN"]
    end
    DB --> DI["Derived-индексы<br/>related · communities · embeddings"]
    DI --> R["Retrieval<br/>text · semantic · hybrid · graph"]
    R --> GR["GraphRAG-поиск<br/>local · global"]
    R --> W["Writer/research<br/>dossier · curation · handoff"]
    DI --> V["Visualization build<br/>агрегации · layout"]
    V --> A["Generated artifacts<br/>JSON · GraphML · offline HTML"]
    W --> A
```

- **Sources** - внешние или локальные источники личных материалов: "Книжный куб", Medium, заметки, документы, архивы ссылок.
- **Ingest** - source adapters, которые получают данные из конкретного источника и сохраняют raw-снимок или читаемый экспорт.
- **Normalize** - преобразование сырого материала в единый формат: текст, метаданные, даты, ссылки, теги, цитаты.
- **Store** - долговременное хранение raw-данных, обработанных документов, индексов и generated outputs в разных зонах.
- **Index/Search** - полнотекстовый (BM25) и семантический (ANN) поиск, graph-aware hybrid-ранжирование, community detection и local/global GraphRAG-поиск поверх графа знаний (GraphRAG-эпик GR-0…GR-6 завершён — см. [graphrag-plan.md](graphrag-plan.md)).
- **Visualize** - реализованные graph exports и offline-представления тем, сообществ, публикаций и ego-связей.
- **Write** - реализованный file-first workflow для evidence dossier, curation и контролируемого round-trip с внешним writing-agent; независимая приёмка ещё не выполнена.

## Подсистемы

### Source adapters

Каждый адаптер отвечает только за один источник или один тип экспорта. Он должен описывать:

- вход: API, экспорт, HTML, Markdown, JSON, CSV или локальная папка;
- выход: raw-снимок и набор нормализуемых элементов;
- ограничения: rate limits, приватность, неполные метаданные, ручные шаги;
- provenance: какие поля позволяют восстановить происхождение материала.

В v1 live URL validation подтверждает public network destination, но не expected host/source authenticity; operator-selected URL доверяется для фиксированного `source_key`. Directory archives считаются trusted owner inputs и не имеют symlink/resource-isolation guarantees. Новые unattended или недоверенные source workflows требуют отдельной host allowlist, filesystem containment и size/quota policy по [ADR 0005](adr/0005-define-source-provenance-and-private-archive-boundaries.md) и [ADR 0006](adr/0006-define-the-local-security-and-privacy-trust-boundary.md).

### Storage

Хранилище должно разделять:

- `raw` - исходные данные без потери контекста;
- `processed` - нормализованные документы и метаданные;
- `generated` - производные материалы: summaries, черновики, отчеты, LLM outputs.

Это разделение важно, чтобы можно было пересобрать базу после изменения нормализации или индексации.

**Как это реализовано.** ArangoDB сейчас является единой зоной хранения: raw snapshot (полный payload или manifest), нормализованные documents/chunks и derived индексы живут в одной базе. Разделение `data/raw` / `data/processed` / `data/generated` в репозитории - это соглашение для on-disk артефактов: `data/raw/` хранит исходные экспорты/снимки, которые вы передаёте адаптерам, а `data/generated/` - выходы `kb export`, `kb viz build` и `kb research`. `data/processed/` зарезервирован и в v1 не материализуется: processed SSOT — ArangoDB (нормализованные documents/chunks и derived indexes). Уточнение границы MCP vs research CLI и processed SSOT — в [ADR 0011](adr/0011-clarify-mcp-vs-research-cli-boundary-and-processed-ssot-in-arangodb.md). Writer/research artifacts по умолчанию публикуются как immutable directories/files под `data/generated/research/`; custom root разрешён только с явным acknowledgement и не отменяет запрет symlinks. При live-ingest по URL сырьё сохраняется только внутри базы (`raw_snapshots.payload`), поэтому для повторного ingest держите исходные снимки в `data/raw/` и передавайте их через `--input`/`--archive`. Provenance сохраняет source traceability, но v1 не фиксирует полный code/config fingerprint и не гарантирует точный historical replay; детали — в [ADR 0005](adr/0005-define-source-provenance-and-private-archive-boundaries.md).

**Модель данных (граф `knowledge_graph`).** Узлы — коллекции документов/сущностей; рёбра — типизированные связи. Similarity-рёбра `item_related_to_item` (chunk↔chunk) и членство `document_in_community` — производные (derived), перестраиваемые.

```mermaid
flowchart TD
    src["sources"]
    raw["raw_snapshots"]
    doc["documents"]
    chunk["chunks"]
    topic["topics"]
    author["authors"]
    work["works"]
    comm["communities"]

    doc -->|document_from_source| src
    chunk -->|chunk_of_document| doc
    chunk -->|chunk_derived_from_raw| raw
    doc -->|document_mentions_topic| topic
    doc -->|document_mentions_author| author
    doc -->|document_references_work| work
    chunk -->|item_related_to_item| chunk
    doc -->|document_in_community| comm
```

### Search and embeddings

Индекс поиска должен строиться поверх `processed`, а не напрямую поверх `raw`. Эмбеддинги и RAG-контекст должны сохранять ссылки на документы и provenance, чтобы любой найденный фрагмент можно было проверить по исходному источнику.

Первый production-like pipeline проектируется вокруг ArangoDB: documents/chunks, graph edges, ArangoSearch full-text и vector indexes живут в одном multi-model ядре. Это снижает количество движущихся частей в v1, но сохраняет явные границы storage/search/vector/graph, чтобы позже вынести отдельный движок при bottleneck.

Эмбеддинги подключаемы через `EmbeddingProvider` (`embedding.provider`): дефолт `hash` — детерминированный, offline, без зависимостей; ingest и запрос строят provider из одного конфига. Провайдер `local` даёт реальные семантические эмбеддинги через `sentence-transformers` (ставится вручную, вне locked-зависимостей). `embedding.dimension` задаёт желаемую размерность, но однородность persisted embedding space и параметры уже существующего vector index не проверяются обычным bootstrap: при смене provider/model/dimension обязателен полный `kb index rebuild --target embeddings`. Revision/fingerprint весов модели пока не хранится. Контракт и ограничения зафиксированы в [ADR 0007](adr/0007-adopt-rebuildable-embeddings-and-extractive-graphrag.md) и [GraphRAG-плане](graphrag-plan.md).

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
  - `kb search local` (GR-5) собирает локальный подграф вокруг сильнейших документов запроса: связывающие сущности, similarity-соседи и сообщества. `kb search global` (GR-5) строит retrieval-conditioned обзор: сопоставляет bounded hybrid candidate pool сообществам и возвращает топ сообществ с summary и документами-цитатами; это не exhaustive проход по всем community summaries. Оба контекста экстрактивные и цитируемые (без LLM). Полный трекинг GraphRAG-подсистемы — [docs/graphrag-plan.md](graphrag-plan.md).
- `kb-mcp` открывает локальный read-only MCP server поверх тех же retrieval/document/graph/source/health операций для других проектов и агентских клиентов.
- `kb export jsonl` пишет generated exports в gitignored data zone; `kb export graph` строит полный doc-level node-link JSON/GraphML, а `kb viz build` — bounded top-K payload и самодостаточный offline HTML.

### MCP integration

MCP слой является интерфейсом чтения поверх `processed`/indexed data. Он не запускает ingest, index rebuild или export, не получает raw snapshot payloads и не выдаёт локальные archive/file paths; document metadata и вложенный provenance проходят явные allowlist-проекции. Сервер работает только через локальный stdio transport. `kb_search` открывает text/semantic/hybrid и local/global GraphRAG режимы; embedding-backed запросы используют тот же configured `EmbeddingProvider` и `retrieval.min_similarity`, что CLI. Tools возвращают agent-ready snippets с `source_key`, `document_key`, `chunk_key`, URL и безопасным raw/import provenance; resources дают `kb://sources` и `kb://documents/{document_key}`. Синхронные handlers и один stdio-клиент за процесс являются принятым ограничением v1. Stdio/read-only не является per-client authorization: local OS user/process с credentials считается доверенным, а imported drafts доступны обычным read surfaces. Shared/remote concurrency или разные local identities требуют отдельного решения с auth/audit моделью; trust boundary — в [ADR 0006](adr/0006-define-the-local-security-and-privacy-trust-boundary.md).

### Visualization

V4 реализован как отдельный read-only слой поверх ArangoDB. `visualizing.py` канонически дедуплицирует document+chunk topic mentions, сворачивает chunk similarity в doc-pairs через `MAX(weight)` и считает community/timeline/ego агрегации. `viz_layouts.py` вычисляет seeded Fruchterman–Reingold для community/topic map и phyllotaxis для документов. Ни один из этих шагов не изменяет БД.

`kb export graph` отдаёт полный doc-level fold, document-topic membership и topic co-occurrence в node-link JSON/GraphML. `kb viz build` ограничивает similarity payload top-K=10, встраивает данные и координаты в пакетный CSP-защищённый HTML и атомарно пишет generated artifact. В интерфейсе есть карта сообществ/топиков, timeline публикаций и двухкольцевой ego-граф документов. Вид книг/авторов отложен: текущий корпус содержит 0 works и только 2 authors.

По умолчанию допускается только `status=published`; drafts требуют `--include-drafts`. Полный текст не экспортируется, но заголовки, URL, даты, темы, topology и community membership всё равно чувствительны. Артефакты не являются источником истины и несут build/index metadata с ограниченными consistency warnings. Команды и wire-контракты — в [visualization.md](visualization.md), решение — в [ADR 0008](adr/0008-adopt-offline-visualization-and-graph-export.md), исторический трекер — в [visualization-plan.md](visualization-plan.md).

### Writing assistant

Writing/research workflow использует базу знаний как контекст для письма, но отделяет черновики и generated outputs от исходных материалов. Любой фрагмент, использованный в публикации или исследовании, должен иметь ссылку на первичный источник.

Runtime [Feature 007](../specs/007-writer-research-workflow/spec.md) реализует пять CLI-операций:

- `kb research build TOPIC` выполняет read-only retrieval по ArangoDB, по умолчанию только для опубликованных документов; source/date/volume filters фиксируются в manifest, а draft evidence требует `--include-drafts`;
- `kb research validate ARTIFACT` проверяет dossier, handoff, входящий writing-output или импортированный output относительно текущего корпуса и связанных локальных артефактов;
- `kb research curate REVISION` применяет упорядоченные `include`/`exclude`/`pin` operations и публикует immutable child revision, не изменяя parent;
- `kb research handoff REVISION` создаёт allowlisted package для `draft` или `summary` только после `--acknowledge-external-disclosure`; для draft evidence требуется отдельный `--allow-draft-evidence`;
- `kb research import-output PACKAGE --handoff HANDOFF` рассматривает возврат writing-agent как недоверенный input, целиком проверяет его и только затем публикует immutable generated output.

Read side не выполняет ingest/index rebuild и не меняет ArangoDB. MCP сохраняется локальным read-only интерфейсом и не получает write tools для dossier или writing artifacts. Контракт закреплён в [ADR 0011](adr/0011-clarify-mcp-vs-research-cli-boundary-and-processed-ssot-in-arangodb.md). File side по умолчанию пишет только в `data/generated/research/`; explicit root вне `data/generated/` требует `--acknowledge-unsafe-output`, а symlink-компоненты запрещены независимо от acknowledgements. Directories/files публикуются атомарно с owner-only `0700`/`0600` permissions на поддерживаемых POSIX-платформах.

Dossier хранит exact selected excerpts, canonical chunk/document/source provenance, digests, порядок evidence и curation lineage. Handoff содержит только выбранный evidence allowlist и writing request; DB credentials, raw payload и локальные source/archive paths в контракт не входят. Imported `draft`/`summary` остаётся generated artifact и явно не становится источником истины. Validation проверяет строгую форму JSON, integrity, citation resolution и structural coverage, но не выполняет автоматическую factual verification и не гарантирует secret redaction. Парсеры и secure artifact I/O не добавляют runtime-зависимостей сверх стандартной библиотеки Python; JSON Schema validation используется только в dev/tests.

Реализация и automated gates готовы, но Feature 007 не считается принятой до четырёх записанных independent acceptance sections: dossier/citation/curation, `draft`, `summary` и privacy/path safety. T050–T053 ещё не выполнялись. Архитектурная граница зафиксирована в принятом [ADR 0010](adr/0010-adopt-provenance-gated-writer-research-file-workflow.md), исполнимый сценарий — в [quickstart](../specs/007-writer-research-workflow/quickstart.md), форма результатов — в [acceptance.md](../specs/007-writer-research-workflow/acceptance.md).

### Architecture Decision Records

Architecture Decision Records живут в [docs/adr](adr/README.md). Они фиксируют значимые решения о данных, privacy, provenance, storage, search/RAG, визуализации и workflow. ADR являются docs-only артефактами и не входят в raw, processed или generated data zones.

Локальный tooling:

- `npm run adr:new` создает следующий нумерованный ADR по шаблону.
- `npm run generate:adr-index` обновляет индекс в `docs/adr/README.md`.
- `npm run check:adr` проверяет метаданные, обязательные RU/EN-секции, связи supersession и свежесть индекса.

### Spec-driven development

По умолчанию новые пользовательские фичи, контракты и source adapters проходят через GitHub Spec Kit: `.specify/` хранит upstream templates/scripts/memory, `.agents/skills/` хранит Codex skills, а `specs/` — спецификации, планы и задачи фичей. Для ограниченных сквозных remediation-, audit-, research-, architecture- и infrastructure-эпиков допустим проверяемый docs plan tracker; значимые решения в обоих workflow требуют ADR. Границы определены в [ADR 0009](adr/0009-scope-spec-kit-and-plan-tracker-workflows.md). Все эти документы являются project artifacts и не входят в data zones.

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
- [Knowledge Base MCP Server spec](../specs/006-knowledge-base-mcp-server/spec.md)
