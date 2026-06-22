# 0001. Ввести docs-only ADR-процесс / Adopt a docs-only ADR process

```json adr-meta
{
  "id": "0001",
  "titleRu": "Ввести docs-only ADR-процесс",
  "titleEn": "Adopt a docs-only ADR process",
  "status": "accepted",
  "date": "2026-06-23",
  "deciders": ["knowledge-base maintainer"],
  "tags": ["adr", "documentation", "process"],
  "supersedes": [],
  "supersededBy": []
}
```

## RU

### Контекст и проблема

`knowledge-base` начинается как персональный проект для сбора данных из разных источников, нормализации, поиска, визуализации и будущих writing/research workflows. Даже на ранней стадии здесь будут решения с долгим хвостом: как хранить raw и processed данные, как фиксировать provenance, какие источники импортировать первыми, как строить retrieval и где проводить границы приватности.

Если такие решения останутся только в переписке или diff, через несколько месяцев будет трудно понять, почему выбран конкретный формат или ограничение.

### Y-statement

В контексте персональной базы знаний, столкнувшись с необходимостью сохранять причины архитектурных решений и компромиссов, мы решили ввести docs-only ADR-процесс, чтобы будущие изменения были объяснимыми и проверяемыми, принимая небольшую цену на поддержание шаблона, индекса и проверки.

### Драйверы решения

- Нужно сохранять контекст решений о данных, приватности, provenance, storage, search и RAG.
- ADR должны быть рядом с кодом и документацией, но не смешиваться с исходными или сгенерированными данными.
- Процесс должен быть легким и воспроизводимым без внешних сервисов.
- Желательно переиспользовать проверенный процесс из `system-design-space`.

### Рассмотренные варианты

- Не вводить ADR и фиксировать решения только в `README.md` или issues.
- Вести свободный `docs/decisions.md` без строгой структуры.
- Ввести docs-only ADR с шаблоном, индексом и локальными проверками.

### Итоговое решение

Выбран вариант: docs-only ADR с двуязычным шаблоном, нумерованными файлами, `adr-meta` JSON-блоком, генерируемым индексом и локальными Node.js-скриптами, потому что он сохраняет контекст решений и остается достаточно легким для персонального проекта.

### Последствия

- Хорошо: важные решения получают явный контекст, статус, дату, deciders, теги и связи supersedes/supersededBy.
- Плохо: при архитектурных изменениях нужно поддерживать дополнительный markdown-документ и индекс.
- Нейтрально: ADR остаются docs-only артефактами и не должны попадать в data pipeline, RAG-индексы или generated outputs без отдельного решения.

### План пересмотра

Пересмотреть процесс, если ADR начинают тормозить небольшие изменения, если проект перейдет на другой основной tooling без Node.js, или если появится необходимость вести решения только на одном языке.

### Ссылки

- [Architectural Decision Records](https://adr.github.io/)
- [ADR process in this repository](README.md)

## EN

### Context and Problem Statement

`knowledge-base` starts as a personal project for collecting data from multiple sources, normalizing it, searching it, visualizing it, and supporting future writing/research workflows. Even at an early stage, the project will make decisions with long-term consequences: how to store raw and processed data, how to preserve provenance, which sources to import first, how to build retrieval, and where to draw privacy boundaries.

If these decisions live only in chat or diffs, it will be hard to understand later why a specific format or constraint was chosen.

### Y-statement

In the context of a personal knowledge database, facing the need to preserve the reasons behind architecture decisions and trade-offs, we decided to adopt a docs-only ADR process to make future changes explainable and reviewable, accepting the small cost of maintaining a template, an index, and checks.

### Decision Drivers

- The project needs to preserve decision context for data, privacy, provenance, storage, search, and RAG.
- ADRs should live next to code and documentation, but stay separate from source data and generated data.
- The process should be lightweight and reproducible without external services.
- Reusing the proven process from `system-design-space` is preferable.

### Considered Options

- Do not introduce ADRs and record decisions only in `README.md` or issues.
- Keep a free-form `docs/decisions.md` without a strict structure.
- Adopt docs-only ADRs with a template, index, and local checks.

### Decision Outcome

Chosen option: docs-only ADRs with a bilingual template, numbered files, an `adr-meta` JSON block, a generated index, and local Node.js scripts, because this preserves decision context while staying lightweight enough for a personal project.

### Consequences

- Good: important decisions get explicit context, status, date, deciders, tags, and supersedes/supersededBy links.
- Bad: architecture changes must maintain an extra markdown document and the index.
- Neutral: ADRs remain docs-only artifacts and must not enter the data pipeline, RAG indexes, or generated outputs without a separate decision.

### Review Plan

Revisit the process if ADRs start slowing down small changes, if the project moves to primary tooling that cannot run Node.js, or if decisions should be maintained in only one language.

### Links

- [Architectural Decision Records](https://adr.github.io/)
- [ADR process in this repository](README.md)
