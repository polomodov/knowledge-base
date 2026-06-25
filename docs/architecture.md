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
- **Index/Search** - полнотекстовый поиск, эмбеддинги, тематические индексы и будущий RAG-контекст.
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

### Search and embeddings

Индекс поиска должен строиться поверх `processed`, а не напрямую поверх `raw`. Эмбеддинги и RAG-контекст должны сохранять ссылки на документы и provenance, чтобы любой найденный фрагмент можно было проверить по исходному источнику.

Первый production-like pipeline проектируется вокруг ArangoDB: documents/chunks, graph edges, ArangoSearch full-text и vector indexes живут в одном multi-model ядре. Это снижает количество движущихся частей в v1, но сохраняет явные границы storage/search/vector/graph, чтобы позже вынести отдельный движок при bottleneck.

Текущий v1 fixture slice реализует этот контур через Python CLI `kb`:

- `kb platform bootstrap` создает коллекции, edge collections, ArangoSearch View, graph definition и vector index.
- `kb ingest fixture` загружает безопасный synthetic fixture и создает source/raw/document/chunk/topic/author/work records.
- `kb index rebuild --target all` идемпотентно проверяет derived search/vector/graph слой.
- `kb search text`, `kb search semantic`, `kb graph neighbors` и `kb search hybrid` возвращают результаты с provenance.
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
