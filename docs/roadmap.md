# Roadmap

Этот roadmap фиксирует направление развития `knowledge-base`. Даты намеренно не указаны: этапы должны двигаться по мере появления реальных источников, данных и рабочих сценариев.

## v0 - Документация и контур проекта

Цель: зафиксировать назначение, принципы и будущую архитектуру до появления кода.

- `README.md` описывает назначение и жизненный цикл данных.
- `AGENTS.md` задает правила для Codex и других агентов.
- `docs/architecture.md` описывает подсистемы и ключевые сущности.
- `docs/roadmap.md` фиксирует этапы развития.
- `docs/adr/` фиксирует архитектурные решения и компромиссы.
- GitHub Spec Kit подключен как официальный spec-driven development workflow для будущих фич.

## v1 - Production knowledge pipeline

Цель: реализовать ArangoDB-centered pipeline для безопасного fixture ingest, provenance, full-text search, vector search, graph traversal и hybrid retrieval.

Реализованный первый срез:

- ArangoDB runtime как multi-model ядро;
- safe fixture ingest без реальных персональных данных;
- коллекции documents/chunks/sources/raw snapshots и edge collections;
- ArangoSearch View для полнотекстового поиска;
- vector index для chunk embeddings;
- graph traversal по source/document/chunk/topic/author/work;
- hybrid retrieval с score breakdown и provenance.
- JSONL export в `data/generated/`;
- unit/integration tests, включая проверку на живой ArangoDB.

## v2 - Первый источник

Цель: реализовать импорт одного реального источника от raw-снимка до нормализованного документа.

Первые источники:

- `tellmeabout.tech`, публичный блог на Medium/custom domain. Adapter работает от RSS/Atom live URL или локального snapshot/export в `data/raw/tellmeabout-tech/`.
- Medium account export. Adapter работает от локального HTML export directory или `.zip` в `data/raw/medium/` и импортирует опубликованные `posts/*.html`.
- "Книжный куб", Telegram-канал. Adapter работает от public `t.me/s/book_cube` HTML snapshot, одиночного Telegram Desktop JSON export или полного владельческого Telegram Desktop JSON archive directory/`.zip` в `data/raw/book-cube/`.

Реализованный первый срез:

- source adapter `tellmeabout-tech`;
- source adapter `medium-export`;
- source adapter `book-cube`;
- owner archive adapter `book-cube-archive`;
- raw snapshot исходного feed/export payload;
- raw manifest snapshot Medium export без сохранения полного приватного HTML payload в одном документе ArangoDB;
- нормализация title/text/url/date/tags/author для блога;
- нормализация Medium post id/title/text/canonical URL/published date/author/export date/image references/link references для опубликованных статей;
- нормализация Telegram message id/text/url/date/hashtags;
- нормализация captions и attachment references для полного Telegram archive import без загрузки media binaries в базу или git;
- topics из feed categories/tags;
- topics из Telegram hashtags;
- provenance для source/raw/document/chunk/topic/author edges;
- unit/integration tests на synthetic Medium-like, Medium export и Telegram fixtures.

## v3 - Поиск, embeddings и GraphRAG ✅

Цель: сделать базу полезной для исследования и RAG-сценариев.

Реализовано (GraphRAG-эпик GR-0…GR-6, PR #22–#33; детальный трекер и диаграммы — [docs/graphrag-plan.md](graphrag-plan.md)):

- полнотекстовый (BM25) и семантический (ANN, `APPROX_NEAR_COSINE`) поиск по нормализованным документам;
- подключаемые эмбеддинги: детерминированный `hash` и локальная модель (`all-mpnet-base-v2`, 768d) с привязкой к chunk/document и provenance; переключение провайдера без re-ingest (`--target embeddings`);
- граф знаний: similarity-рёбра `item_related_to_item` (`--target related`), graph-neighborhood boosts в hybrid-ранжировании (`graph_boost`) и расширение кандидатов графом;
- community detection (Louvain) и экстрактивные community summaries (`--target communities`);
- GraphRAG local/global поиск поверх графа и сообществ (`kb search local` / `global`);
- relevance-gated recall и CLI-команды поиска;
- локальный read-only MCP server для search, GraphRAG, раскрытия документов, graph neighbors, source inventory и health;
- unit/integration-тесты на воспроизводимость индексации и ранжирования.

## v4 - Визуализация

Цель: увидеть связи внутри базы знаний.

- карта тем и тегов;
- связи между источниками, книгами, авторами и собственными текстами;
- временная шкала публикаций и заметок;
- экспорт графа или простой frontend для исследования.

Детальный план реализации (форма: `kb export graph` + самодостаточный статический HTML `kb viz build`; шаги V4-0…V4-6 отдельными PR) — в [docs/visualization-plan.md](visualization-plan.md).

## v5 - Writer/research workflow

Цель: использовать knowledge database при написании постов, исследований и книг.

- подбор релевантных фрагментов под тему;
- цитирование с provenance;
- сбор исследовательских подборок;
- черновики и summaries в `generated`;
- проверка, что generated outputs отделены от исходной базы.

## Текущий статус

Завершены v1 fixture pipeline; v2 source adapters (`tellmeabout.tech`, Medium account export, public/snapshot import для "Книжного куба" и полный владельческий Telegram archive import); и **v3 — GraphRAG-эпик (GR-0…GR-6): семантические эмбеддинги (`all-mpnet-base-v2`, 768d), граф-осведомлённый hybrid, community detection, local/global GraphRAG-поиск и локальный read-only MCP server** (детали и диаграммы — [docs/graphrag-plan.md](graphrag-plan.md)).

**Аудит реализации (июль 2026) полностью отработан:** все 46 находок устранены и смерджены в `main` — единый `topic_key`, провенанс и честный дедуп (`created_at`, корректные счётчики), качество retrieval (дедуп выдачи, корректный фьюжн скора, реальное использование vector index, устранение N+1), безопасность и приватность (учётные данные, валидация fetch-URL/SSRF, path traversal, зона экспорта), инженерная гигиена (общий `ingest_core`, ruff + mypy + pytest-cov, CI против ArangoDB service-container) и робастность парсеров источников. Подробности и трекер MR - в [docs/implementation-audit-plan.md](implementation-audit-plan.md).

Следующий фокус - визуализация тем, источников и связей внутри базы знаний (v4) и writer/research workflow поверх неё (v5).
