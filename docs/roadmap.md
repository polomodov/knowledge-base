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

## v3 - Поиск, embeddings и GraphRAG

Цель: сделать базу полезной для исследования и RAG-сценариев.

- полнотекстовый поиск по нормализованным документам;
- подготовка chunks или фрагментов для retrieval;
- эмбеддинги с привязкой к document/item и provenance;
- graph-neighborhood boosts и GraphRAG context;
- простой CLI или notebook для поиска по базе;
- тесты на воспроизводимость индексации.

## v4 - Визуализация

Цель: увидеть связи внутри базы знаний.

- карта тем и тегов;
- связи между источниками, книгами, авторами и собственными текстами;
- временная шкала публикаций и заметок;
- экспорт графа или простой frontend для исследования.

## v5 - Writer/research workflow

Цель: использовать knowledge database при написании постов, исследований и книг.

- подбор релевантных фрагментов под тему;
- цитирование с provenance;
- сбор исследовательских подборок;
- черновики и summaries в `generated`;
- проверка, что generated outputs отделены от исходной базы.

## Текущий статус

Сейчас завершены v1 fixture pipeline и v2 source adapters: `tellmeabout.tech`, Medium account export, public/snapshot import для "Книжного куба" и полный владельческий Telegram archive import.

По итогам аудита реализации (июль 2026) ближайший фокус - устранение находок в порядке приоритета, зафиксированном в [docs/implementation-audit-plan.md](implementation-audit-plan.md):

1. целостность тем - единый `topic_key` для всех адаптеров;
2. провенанс и честный дедуп (`created_at`, корректные счётчики);
3. качество retrieval - дедуп выдачи, корректный фьюжн скора, реальное использование vector index;
4. безопасность и приватность - учётные данные, валидация fetch-URL, экспорт;
5. инженерная гигиена - общий ingest core, lint/type/coverage, CI;
6. робастность парсеров источников.

Параллельно - прогон реальных локальных archives/snapshots из `data/raw/` и расширение качества extraction/retrieval.
