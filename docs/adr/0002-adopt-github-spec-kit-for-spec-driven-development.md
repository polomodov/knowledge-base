# 0002. Ввести GitHub Spec Kit для spec-driven development / Adopt GitHub Spec Kit for spec-driven development

```json adr-meta
{
  "id": "0002",
  "titleRu": "Ввести GitHub Spec Kit для spec-driven development",
  "titleEn": "Adopt GitHub Spec Kit for spec-driven development",
  "status": "accepted",
  "date": "2026-06-23",
  "deciders": ["knowledge-base maintainer"],
  "tags": ["spec-driven-development", "agent-workflow", "process"],
  "supersedes": [],
  "supersededBy": []
}
```

## RU

### Контекст и проблема

`knowledge-base` должен развиваться как персональная база знаний с импортом источников, provenance, поиском, визуализацией и writing/research workflows. Для таких фич легко потерять границы между продуктовым намерением, технической реализацией, приватными данными и сгенерированными результатами.

Проекту нужен процесс, в котором фича начинается со спецификации, затем получает технический план, задачи и только потом реализацию. Это должно позволить агентам работать автономнее, но в рамках явных требований, ограничений приватности и проверяемых артефактов.

### Y-statement

В контексте персональной knowledge database и автономной agent-driven разработки, столкнувшись с риском неструктурированных фич и скрытых архитектурных решений, мы решили использовать официальный GitHub Spec Kit с Codex-интеграцией, чтобы вести фичи через спецификации, планы и задачи, принимая зависимость от `specify` CLI, `uv` и upstream-шаблонов Spec Kit.

### Драйверы решения

- Фичи должны начинаться с WHAT/WHY-спецификации, а не с преждевременного кода.
- Агенту нужен воспроизводимый workflow: constitution, specify, plan, tasks, implement, converge.
- Процесс должен сохранять privacy/provenance-инварианты проекта.
- Предпочтительны официальные инструменты вместо самописной имитации SDD.
- Спецификации должны быть project artifacts, но не raw, processed или generated knowledge data.

### Рассмотренные варианты

- **GitHub Spec Kit.** Open-source toolkit с `specify` CLI, Codex integration, skills в `.agents/skills/`, shared templates в `.specify/` и workflow `constitution -> specify -> plan -> tasks -> implement`.
- **Kiro-style specs.** Практичная модель `requirements.md`, `design.md`, `tasks.md`, но она сильнее привязана к продуктовой среде Kiro и не дает такого же portable CLI для Codex.
- **Repo-local lightweight templates.** Самые дешевые markdown-шаблоны без внешнего tooling, но их нужно поддерживать самостоятельно; агенты быстрее начнут расходиться в деталях процесса.
- **Cucumber/Gherkin.** Хороший формат acceptance-сценариев, но не заменяет полный feature discovery, planning и task breakdown.
- **OpenAPI-first.** Полезно для API-контрактов, но не является общим SDD-framework для источников данных, CLI, storage, visualization и writing workflows.

### Итоговое решение

Выбран вариант: GitHub Spec Kit как основной spec-driven development framework проекта.

Инструментальный способ внедрения:

```bash
brew install uv
uv tool install specify-cli --from git+https://github.com/github/spec-kit.git@v0.11.5
specify init --here --force --integration codex --script sh
```

Spec Kit инициализируется в текущем репозитории и добавляет upstream-артефакты `.specify/`, `.agents/skills/`, templates, scripts, memory и Codex agent context. Git extension Spec Kit сейчас не включается; к нему нужно вернуться отдельным решением после первого коммита и стабилизации workflow.

Feature specs по умолчанию пишутся на русском с кратким English summary, чтобы сохранить естественный рабочий язык проекта и при этом оставить компактный англоязычный контекст для инструментов и будущих внешних интеграций.

### Последствия

- Хорошо: фичи получают единый путь от намерения к реализации, а Codex может работать автономнее через официальные skills.
- Хорошо: спецификации, планы и задачи становятся проверяемыми project artifacts рядом с кодом.
- Плохо: проект получает зависимость от `uv`, `specify` CLI и upstream-структуры Spec Kit.
- Плохо: обновления Spec Kit могут менять templates, scripts и managed agent context; их нужно ревьюить как tooling changes.
- Нейтрально: specs не являются raw/processed/generated knowledge data и не должны смешиваться с импортированными персональными данными.
- Нейтрально: существующие правила `knowledge-base` в `AGENTS.md` имеют приоритет для privacy, provenance и разделения data zones.

### План пересмотра

Пересмотреть решение, если Spec Kit перестанет поддерживать Codex integration, если процесс окажется слишком тяжелым для персонального проекта, если появится необходимость включить git extension или если будущая реализация потребует другого SDD-подхода для research/data workflows.

### Ссылки

- [GitHub Spec Kit](https://github.com/github/spec-kit)
- [Spec Kit methodology](https://github.com/github/spec-kit/blob/main/spec-driven.md)
- [Spec Kit CLI reference](https://github.github.com/spec-kit/reference/overview.html)
- [Spec Kit integrations reference](https://github.github.com/spec-kit/reference/integrations.html)
- [Kiro specs](https://kiro.dev/docs/specs/)
- [Cucumber Gherkin reference](https://cucumber.io/docs/gherkin/reference/)
- [OpenAPI Specification](https://spec.openapis.org/oas/latest.html)

## EN

### Context and Problem Statement

`knowledge-base` should evolve as a personal knowledge database with source ingestion, provenance, search, visualization, and writing/research workflows. For such features, it is easy to blur the boundaries between product intent, technical implementation, private data, and generated outputs.

The project needs a process where each feature starts with a specification, then receives a technical plan, tasks, and only then implementation. This should let agents work more autonomously while staying inside explicit requirements, privacy constraints, and reviewable artifacts.

### Y-statement

In the context of a personal knowledge database and autonomous agent-driven development, facing the risk of unstructured features and hidden architecture decisions, we decided to use official GitHub Spec Kit with Codex integration to move features through specifications, plans, and tasks, accepting the dependency on the `specify` CLI, `uv`, and upstream Spec Kit templates.

### Decision Drivers

- Features should start from WHAT/WHY specifications instead of premature code.
- Agents need a reproducible workflow: constitution, specify, plan, tasks, implement, converge.
- The process must preserve the project's privacy/provenance invariants.
- Official tooling is preferred over a local imitation of SDD.
- Specifications should be project artifacts, but not raw, processed, or generated knowledge data.

### Considered Options

- **GitHub Spec Kit.** Open-source toolkit with the `specify` CLI, Codex integration, skills in `.agents/skills/`, shared templates in `.specify/`, and the workflow `constitution -> specify -> plan -> tasks -> implement`.
- **Kiro-style specs.** A practical `requirements.md`, `design.md`, `tasks.md` model, but more tied to the Kiro product environment and without the same portable CLI for Codex.
- **Repo-local lightweight templates.** The cheapest markdown-only approach without external tooling, but it would need local maintenance and agents would drift more easily.
- **Cucumber/Gherkin.** A good acceptance-scenario format, but not a replacement for full feature discovery, planning, and task breakdown.
- **OpenAPI-first.** Useful for API contracts, but not a general SDD framework for data sources, CLI, storage, visualization, and writing workflows.

### Decision Outcome

Chosen option: GitHub Spec Kit as the project's primary spec-driven development framework.

Tooling adoption path:

```bash
brew install uv
uv tool install specify-cli --from git+https://github.com/github/spec-kit.git@v0.11.5
specify init --here --force --integration codex --script sh
```

Spec Kit is initialized in the current repository and adds upstream artifacts: `.specify/`, `.agents/skills/`, templates, scripts, memory, and Codex agent context. The Spec Kit git extension is not enabled now; revisit it in a separate decision after the first commit and workflow stabilization.

Feature specs are written in Russian by default with a short English summary, preserving the project's natural working language while keeping compact English context for tools and future external integrations.

### Consequences

- Good: features get a single path from intent to implementation, and Codex can work more autonomously through official skills.
- Good: specifications, plans, and tasks become reviewable project artifacts next to code.
- Bad: the project gains a dependency on `uv`, the `specify` CLI, and upstream Spec Kit structure.
- Bad: Spec Kit upgrades may change templates, scripts, and managed agent context; review them as tooling changes.
- Neutral: specs are not raw/processed/generated knowledge data and must not be mixed with imported personal data.
- Neutral: existing `knowledge-base` rules in `AGENTS.md` take priority for privacy, provenance, and data-zone separation.

### Review Plan

Revisit this decision if Spec Kit stops supporting Codex integration, if the process becomes too heavy for a personal project, if the git extension needs to be enabled, or if future implementation work requires a different SDD approach for research/data workflows.

### Links

- [GitHub Spec Kit](https://github.com/github/spec-kit)
- [Spec Kit methodology](https://github.com/github/spec-kit/blob/main/spec-driven.md)
- [Spec Kit CLI reference](https://github.github.com/spec-kit/reference/overview.html)
- [Spec Kit integrations reference](https://github.github.com/spec-kit/reference/integrations.html)
- [Kiro specs](https://kiro.dev/docs/specs/)
- [Cucumber Gherkin reference](https://cucumber.io/docs/gherkin/reference/)
- [OpenAPI Specification](https://spec.openapis.org/oas/latest.html)
