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

Кандидат по умолчанию: "Книжный куб", если доступ к данным проще и важнее для первого вертикального среза. Medium можно выбрать первым, если экспорт или API окажутся надежнее.

Ожидаемый результат:

- один source adapter;
- raw-сохранение исходных данных;
- минимальная нормализация текста и метаданных;
- сохранение provenance для каждого элемента;
- базовые тесты на импорт и нормализацию.

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

Сейчас завершен первый production-like fixture slice из v1. Следующий фокус - v2: выбрать первый реальный источник, описать его через Spec Kit и реализовать source adapter без попадания персональных raw-данных в git.
