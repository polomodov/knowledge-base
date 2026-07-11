# 0008. Выбрать офлайн-визуализацию и экспорт графа / Adopt offline visualization and graph export

```json adr-meta
{
  "id": "0008",
  "titleRu": "Выбрать офлайн-визуализацию и экспорт графа",
  "titleEn": "Adopt offline visualization and graph export",
  "status": "accepted",
  "date": "2026-07-11",
  "deciders": [
    "knowledge-base maintainer"
  ],
  "tags": [
    "visualization",
    "graph-export",
    "offline",
    "privacy"
  ],
  "supersedes": [],
  "supersededBy": []
}
```

## RU

### Контекст и проблема

Граф знаний уже содержит документы, темы, сообщества, временные метаданные и связи сходства. Для исследования корпуса нужны как интерактивные представления, так и стандартные файлы для внешних инструментов. При этом база персональная: визуализация должна работать локально, не отправлять данные в сторонние сервисы и не превращаться в ещё один постоянно работающий сервер или JavaScript build stack.

Экспорт и HTML являются производными артефактами, а не источником истины. Им нужны воспроизводимая сборка, provenance и сведения о свежести; приватные черновики и полный текст документов не должны незаметно попадать в переносимые файлы.

Этот ADR ретроспективно записывает решение, ранее принятое и подробно зафиксированное в [плане v4](../visualization-plan.md); он не являлся предварительным одобрением выбранной формы. Статус `accepted` фиксирует действующий архитектурный выбор, но не утверждает, что все шаги V4 уже реализованы: фактический статус реализации остаётся в плане.

### Y-statement

В контексте локального исследования персонального графа знаний, столкнувшись с требованиями приватности, переносимости и минимальной эксплуатационной сложности, мы решили выпускать стандартные графовые экспорты и самодостаточный офлайн-HTML, чтобы визуализации открывались без сервера и внешних зависимостей, принимая необходимость пересобирать статические артефакты и ограничить сложность клиентской части.

### Драйверы решения

- Данные должны оставаться локальными и открываться без сетевых запросов, CDN и внешних аналитических сервисов.
- Экспорт должен быть пригоден и для программной обработки, и для Gephi, yEd и других графовых инструментов.
- Артефакты должны воспроизводимо строиться одной CLI-командой из канонической БД.
- Raw-, processed- и generated-зоны должны оставаться разделёнными; визуализация не становится новым хранилищем истины.
- Большая часть логики должна быть детерминированной и тестируемой без браузерного toolchain.
- Переносимый файл не должен по умолчанию расширять область раскрытия черновиков или полного текста документов.

### Рассмотренные варианты

- **Локальное web-приложение с API и frontend framework.** Даёт богатую интерактивность и живые данные, но добавляет сервер, npm-сборку, зависимости, поверхность атаки и эксплуатацию.
- **Hosted/CDN-визуализация.** Упрощает доставку библиотек, но создаёт сетевые запросы и неприемлемый риск передачи метаданных персонального корпуса третьим сторонам.
- **Только notebooks или внешние инструменты.** Удобно для разового анализа, но не даёт воспроизводимого встроенного обзора корпуса и требует отдельного окружения.
- **Стандартные экспорты плюс самодостаточный статический HTML.** Сохраняет переносимость и офлайн-режим, оставляя агрегацию и layout в тестируемом Python, а браузеру — тонкий слой отображения.

### Итоговое решение

Выбран вариант: стандартные графовые экспорты плюс самодостаточный статический HTML.

1. `kb export graph` формирует **полный**, без top-K pruning, doc-level fold в двух форматах: node-link JSON для программного потребления и GraphML для внешних инструментов. Для chunk similarity пар документов получает `MAX(weight)`, а `chunk_pairs` остаётся display-only счётчиком; topic co-occurrence дедуплицирует document identity, потому что mention edges существуют и от документа, и от его chunks. Семантика узлов, рёбер, идентификаторов и весов совпадает между JSON и GraphML.
2. Экспорт содержит идентификаторы и необходимые для анализа метаданные, но не тела документов и не тексты чанков. Это не делает артефакт анонимным: заголовки, URL, topic labels, timestamps, community membership, topology, co-occurrence и similarity weights потенциально чувствительны и сопровождаются предупреждением о generated export zone.
3. `kb viz build` атомарно записывает самодостаточный HTML в `data/generated/`: данные, стили и классический vanilla JavaScript встроены в один файл. Артефакт работает из `file://` без CDN, `fetch`, локального сервера и npm-сборки.
4. Поддерживаются три представления: карта сообществ и тем, временная шкала публикаций и выборочный ego-граф документов. HTML использует ограниченный top-K=10 doc-similarity payload для размера и интерактивности, тогда как graph export остаётся полным. Оба display fold используют `MAX`, что намеренно отличается от `SUM` всех chunk-pair weights в Louvain/community semantics ADR 0007. Агрегации, пороги и детерминированные layout-координаты вычисляются в Python; JavaScript отвечает главным образом за отображение и взаимодействие.
5. Динамический текст вставляется только безопасными DOM API. Встроенный JSON экранирует границы `<script>`, а кликабельные ссылки проходят allowlist схем `http`/`https`. Кириллица, emoji и другие Unicode-данные сохраняются без потерь.
6. По умолчанию экспортируются только документы со статусом `published`; черновики требуют явного `--include-drafts`. Payload содержит provenance и freshness metadata: время сборки, источник/БД, счётчики, embedding model и состояния производных индексов. Планируемые warnings покрывают известные рассогласования — `communities` старше `related` и пустой `related` при наличии embeddings, — но не сравнивают indexes с последним import/content revision; новый ingest может оставить оба derived слоя устаревшими без предупреждения.
7. Запись выполняется через временный файл и атомарную замену. Для самодостаточного HTML действует проверяемый бюджет с потолком 5 MB; приближение к нему или превышение нельзя принимать молча — это сигнал пересмотреть способ доставки данных.
8. Автоматические тесты проверяют Python-агрегации, сериализацию, XSS/URL-контракты, атомарность, размер и структуру HTML. Acceptance также включает ручное открытие HTML из `file://` в Chrome, Firefox и Safari и ручную проверку GraphML в Gephi; эти проверки документируются, но не подменяются ложной browser-автоматизацией.

### Последствия

- Хорошо: визуализация полностью локальна, переносима и не требует постоянно работающего приложения.
- Хорошо: JSON и GraphML позволяют исследовать тот же срез стандартными внешними инструментами.
- Хорошо: тяжёлая логика остаётся в Python и покрывается обычными unit- и integration-тестами.
- Хорошо: явная политика draft/provenance и metadata с известными consistency warnings снижают риск принять derived artifact за канонические данные, не обещая полной freshness guarantee.
- Плохо: HTML является снимком и должен пересобираться после изменений данных или индексов.
- Плохо: freshness относительно нового ingest не определяется автоматически; mutually consistent, но устаревшие `related`/`communities` могут попасть в артефакт без warning.
- Плохо: vanilla JavaScript и лимит одного файла ограничивают сложность интерфейса и масштаб корпуса.
- Нейтрально: generated-артефакты игнорируются git и даже без полного текста раскрывают чувствительные metadata и структуру интересов/связей; владелец отвечает за их безопасное распространение.

### План пересмотра

Пересмотреть решение, если типичный HTML приближается к потолку 5 MB, построение или браузерный рендер перестают укладываться в приемлемое время, потребуется строгая freshness guarantee через import/content revision, появляется многопользовательский или удалённый сценарий, требуется live-update либо vanilla JS больше не позволяет доступно поддерживать выбранные виды. Переход к серверу, внешнему хранилищу данных или frontend framework требует нового ADR с отдельной моделью приватности.

### Ссылки

- [План реализации v4](../visualization-plan.md)
- [Архитектура knowledge-base](../architecture.md)
- [ADR 0003: ArangoDB-centered production pipeline](0003-adopt-arangodb-centered-production-pipeline.md)
- [ADR 0005: границы источников, provenance и приватных архивов](0005-define-source-provenance-and-private-archive-boundaries.md)
- [ADR 0006: локальная граница безопасности и приватности](0006-define-the-local-security-and-privacy-trust-boundary.md)
- [ADR 0007: пересобираемые эмбеддинги и экстрактивный GraphRAG](0007-adopt-rebuildable-embeddings-and-extractive-graphrag.md)

## EN

### Context and Problem Statement

The knowledge graph already contains documents, topics, communities, temporal metadata, and similarity links. Exploring the corpus requires both interactive views and standard files for external tools. At the same time, this is a personal database: visualization must run locally, avoid sending data to third-party services, and avoid becoming another continuously running server or JavaScript build stack.

Exports and HTML are derived artifacts rather than sources of truth. They need reproducible builds, provenance, and freshness information; private drafts and full document text must not silently enter portable files.

This ADR retrospectively records the decision previously made and described in detail in the [v4 plan](../visualization-plan.md); it was not prior approval of the selected form. The `accepted` status captures the active architectural choice, but does not claim that every V4 step is already implemented; implementation status remains in the plan.

### Y-statement

In the context of local exploration of a personal knowledge graph, facing privacy, portability, and operational-simplicity requirements, we decided for standard graph exports and a self-contained offline HTML artifact to make visualizations usable without a server or external dependencies, accepting the need to rebuild static artifacts and constrain client-side complexity.

### Decision Drivers

- Data must remain local and open without network requests, CDNs, or external analytics services.
- Exports must support both programmatic processing and graph tools such as Gephi and yEd.
- Artifacts must be reproducibly built from the canonical database with one CLI command.
- Raw, processed, and generated zones must remain separate; visualization does not become a new source of truth.
- Most logic must be deterministic and testable without a browser toolchain.
- A portable file must not expand disclosure of drafts or full document text by default.

### Considered Options

- **A local web application with an API and frontend framework.** It provides rich interaction and live data, but adds a server, npm builds, dependencies, attack surface, and operations.
- **Hosted/CDN visualization.** It simplifies library delivery, but creates network requests and an unacceptable risk of sharing personal-corpus metadata with third parties.
- **Notebooks or external tools only.** They are useful for one-off analysis, but provide no reproducible built-in corpus overview and require a separate environment.
- **Standard exports plus self-contained static HTML.** This preserves portability and offline operation, keeps aggregation and layout in testable Python, and leaves a thin rendering layer in the browser.

### Decision Outcome

Chosen option: standard graph exports plus self-contained static HTML.

1. `kb export graph` produces a **complete** doc-level fold without top-K pruning in two formats: node-link JSON for programmatic consumption and GraphML for external tools. For chunk similarity, a document pair receives `MAX(weight)`, while `chunk_pairs` remains a display-only count; topic co-occurrence deduplicates document identity because mention edges exist from both a document and its chunks. Node, edge, identifier, and weight semantics match across JSON and GraphML.
2. Exports contain identifiers and metadata required for analysis, but no document bodies or chunk text. This does not anonymize the artifact: titles, URLs, topic labels, timestamps, community membership, topology, co-occurrence, and similarity weights remain potentially sensitive and are covered by a generated-export-zone warning.
3. `kb viz build` atomically writes a self-contained HTML file under `data/generated/`: data, styles, and classic vanilla JavaScript are embedded in one file. The artifact works from `file://` without a CDN, `fetch`, a local server, or an npm build.
4. Three views are supported: a community and topic map, a publication timeline, and a selected document ego graph. HTML uses a bounded top-K=10 document-similarity payload for size and interaction, while graph export remains complete. Both display folds use `MAX`, deliberately differing from the `SUM` of all chunk-pair weights in ADR 0007's Louvain/community semantics. Aggregations, thresholds, and deterministic layout coordinates are computed in Python; JavaScript primarily handles rendering and interaction.
5. Dynamic text is inserted only through safe DOM APIs. Embedded JSON escapes `<script>` boundaries, and clickable links pass an `http`/`https` scheme allowlist. Cyrillic, emoji, and other Unicode data are preserved losslessly.
6. Only documents with `status == "published"` are exported by default; drafts require explicit `--include-drafts`. The payload includes provenance and freshness metadata: build time, source/database, counts, embedding model, and derived-index states. Planned warnings cover known inconsistencies — `communities` older than `related`, and empty `related` with embeddings present — but do not compare indexes with the latest import/content revision; a new ingest may leave both derived layers stale without a warning.
7. Writes use a temporary file followed by atomic replacement. Self-contained HTML has a testable 5 MB budget ceiling; approaching or exceeding it must not be accepted silently and triggers review of the data-delivery mechanism.
8. Automated tests cover Python aggregations, serialization, XSS/URL contracts, atomicity, size, and HTML structure. Acceptance also includes manually opening the HTML from `file://` in Chrome, Firefox, and Safari and manually checking GraphML in Gephi; these checks are documented without pretending that browser behavior is covered by the Python suite.

### Consequences

- Good: visualization is fully local, portable, and requires no continuously running application.
- Good: JSON and GraphML enable exploration of the same slice with standard external tools.
- Good: complex logic remains in Python and is covered by ordinary unit and integration tests.
- Good: explicit draft/provenance policy and metadata with known consistency warnings reduce the risk of treating a derived artifact as canonical data without promising a complete freshness guarantee.
- Bad: HTML is a snapshot and must be rebuilt after data or index changes.
- Bad: freshness relative to new ingest is not detected automatically; mutually consistent but stale `related`/`communities` may enter an artifact without a warning.
- Bad: vanilla JavaScript and the single-file limit constrain interface complexity and corpus scale.
- Neutral: generated artifacts are gitignored and still expose sensitive metadata and interest/relationship structure even without full text; the owner is responsible for distributing them safely.

### Review Plan

Revisit the decision if typical HTML approaches the 5 MB ceiling, build or browser rendering time becomes unacceptable, a strict freshness guarantee through import/content revision is required, a multi-user or remote scenario appears, live updates are required, or vanilla JavaScript can no longer maintain the chosen views accessibly. Moving to a server, external data storage, or a frontend framework requires a new ADR with a separate privacy model.

### Links

- [v4 implementation plan](../visualization-plan.md)
- [knowledge-base architecture](../architecture.md)
- [ADR 0003: ArangoDB-centered production pipeline](0003-adopt-arangodb-centered-production-pipeline.md)
- [ADR 0005: source, provenance, and private archive boundaries](0005-define-source-provenance-and-private-archive-boundaries.md)
- [ADR 0006: local security and privacy trust boundary](0006-define-the-local-security-and-privacy-trust-boundary.md)
- [ADR 0007: rebuildable embeddings and extractive GraphRAG](0007-adopt-rebuildable-embeddings-and-extractive-graphrag.md)
