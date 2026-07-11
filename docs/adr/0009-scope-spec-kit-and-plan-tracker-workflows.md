# 0009. Разделить область применения Spec Kit и plan-tracker workflow / Scope Spec Kit and plan-tracker workflows

```json adr-meta
{
  "id": "0009",
  "titleRu": "Разделить область применения Spec Kit и plan-tracker workflow",
  "titleEn": "Scope Spec Kit and plan-tracker workflows",
  "status": "accepted",
  "date": "2026-07-11",
  "deciders": [
    "knowledge-base maintainer"
  ],
  "tags": [
    "spec-driven-development",
    "agent-workflow",
    "process"
  ],
  "supersedes": [
    "0002"
  ],
  "supersededBy": []
}
```

## RU

### Контекст и проблема

[ADR 0002](0002-adopt-github-spec-kit-for-spec-driven-development.md) ввёл GitHub Spec Kit как основной путь от требований к реализации. Он хорошо сработал для production pipeline, source adapters и MCP-интерфейса: в этих фичах важно заранее зафиксировать пользовательский контракт, сценарии, provenance и границы приватности.

Позже GraphRAG-эпик и проектирование визуализации v4 велись через подробные docs-планы со скоупом, зависимостями, критериями приёмки, статусами и проверками по отдельным PR. Для ограниченных сквозных эпиков, которые совершенствуют уже определённую систему, полный комплект Spec Kit иногда дублирует такой трекер и затрудняет поддержание единого актуального плана. Одновременно неформальная работа без обязательного состава артефакта снова создала бы риск скрытых решений.

Этот ADR ретроспективно закрывает расхождение между формулировкой ADR 0002 и уже применёнными изменениями: GraphRAG-эпиком GR-0…GR-6 из [GraphRAG-плана](../graphrag-plan.md) и выбранной формой v4 из [плана визуализации](../visualization-plan.md). Он не изображает эти изменения как предварительно одобренные через ADR, а явно фиксирует происхождение нового правила. Текст исторического решения ADR 0002 не переписывается; меняются только его статус на `superseded` и ссылка на этот ADR.

### Y-statement

В контексте agent-driven разработки персональной базы знаний, столкнувшись как с избыточностью полного Spec Kit для некоторых ограниченных сквозных эпиков, так и с риском неформальных планов, мы решили выбрать scoped hybrid workflow, чтобы сохранить проверяемые требования и трассируемость при соразмерной стоимости процесса, принимая необходимость явно классифицировать работу и поддерживать два стандартизованных вида планирующих артефактов.

### Драйверы решения

- Новые пользовательские возможности и источники данных требуют явных WHAT/WHY, сценариев, контрактов и границ приватности до реализации.
- Сквозные remediation-, research- и architecture-эпики часто начинаются с уже согласованного скоупа и затрагивают множество существующих подсистем.
- Любой облегчённый workflow должен сохранять зависимости, критерии приёмки, статус, валидацию и причины значимых решений.
- ADR должен фиксировать архитектурные выборы независимо от выбранной формы планирования.
- Мелкие исправления и локальные рефакторинги не должны получать церемонию, несоразмерную риску.
- Агентам и ревьюерам нужна однозначная граница между разрешённым plan tracker и неструктурированным списком заметок.

### Рассмотренные варианты

- **Spec Kit для каждого изменения.** Даёт единообразие, но создаёт лишние specs/plans/tasks для локальных исправлений и эпиков, уже описанных детальным техническим трекером.
- **Свободный выбор процесса без контракта.** Минимизирует формальности, но приводит к неполным планам, скрытым решениям и расхождению статусов.
- **Spec Kit только для source adapters.** Слишком узко: новые пользовательские интерфейсы и возможности также нуждаются в feature-контракте.
- **Scoped hybrid workflow.** Оставляет Spec Kit по умолчанию для новых пользовательских фич и допускает проверяемый docs plan tracker для ограниченного класса сквозных работ.

### Итоговое решение

Выбран вариант: scoped hybrid workflow. Этот ADR заменяет ADR 0002 и сохраняет Spec Kit как default, но не универсальный, workflow.

1. **Spec Kit используется по умолчанию** для новых пользовательских возможностей, новых feature/API/CLI-контрактов, source adapters и import workflows, а также для неоднозначных инициатив, где до реализации нужно уточнить требования. Ожидаемый путь: specification → plan → tasks → implementation/convergence; для неоднозначностей применяется clarify, перед реализацией — consistency analysis.
2. **Docs plan tracker допустим** для ограниченного сквозного remediation-, audit-, research-, architecture- или infrastructure-эпика, когда владелец уже явно зафиксировал цель и границы, а работа в основном изменяет несколько существующих подсистем. Причина выбора tracker вместо Spec Kit должна быть записана в самом плане или связанной ADR.
3. Такой tracker обязан содержать: явный scope и out-of-scope; принятые решения и открытые вопросы; зависимости и порядок шагов; критерии приёмки; статус каждого шага; план проверки и фактическую валидацию; ссылки на связанные ADR, PR или коммиты. Один tracker является каноническим источником статуса эпика.
4. Plan tracker **не заменяет ADR**. Значимые решения о границах данных, provenance, storage, search/RAG, приватности, визуализации, автоматизации или процессе записываются отдельными ADR по обычному контракту.
5. Простое исправление дефекта, теста, документации, локальный рефакторинг или обслуживание tooling не требуют полного Spec Kit либо отдельного plan tracker, если не меняют внешний или архитектурный контракт. Их объём и проверка должны быть понятны из issue/запроса, diff, тестов и commit/PR description.
6. Перед завершением архитектурной фичи diff проверяется на ADR-значимые решения. Если пробел обнаружен после реализации, допустим ретроспективный `accepted` ADR: он обязан назвать исходный план, PR, коммит или набор изменений и прямо сказать, что не являлся предварительным одобрением.

### Последствия

- Хорошо: Spec Kit остаётся предсказуемым стандартом для фич, где требования и пользовательский контракт важнее технического трекера.
- Хорошо: ограниченные сквозные эпики могут использовать один подробный и актуальный план без дублирования артефактов.
- Хорошо: обязательный состав tracker и независимый ADR-контракт сохраняют проверяемость и причины решений.
- Плохо: перед началом работы нужно классифицировать инициативу; неверная классификация может привести к недостаточной спецификации.
- Плохо: два workflow требуют дисциплины, чтобы docs tracker не превратился в произвольный список задач.
- Нейтрально: уже созданные Spec Kit artifacts остаются действующими project artifacts; этот ADR не мигрирует и не удаляет их.
- Нейтрально: ретроспективный ADR закрывает пробел в журнале решений, но не меняет исторический порядок одобрения изменений.

### План пересмотра

Пересмотреть решение, если агенты регулярно выбирают облегчённый путь для неоднозначных пользовательских фич, trackers теряют обязательные поля или расходятся с реализацией, Spec Kit заметно упростит сквозные эпики, появится Git extension либо процесс станет многопользовательским и потребует более строгого единого governance. При повторяющихся ошибках классификации следует добавить автоматическую проверку или decision checklist.

### Ссылки

- [ADR 0002: ввести GitHub Spec Kit](0002-adopt-github-spec-kit-for-spec-driven-development.md)
- [AGENTS.md: правила Spec-Driven Development](../../AGENTS.md#spec-driven-development)
- [README: Spec-Driven Development](../../README.md#spec-driven-development)
- [GraphRAG plan tracker](../graphrag-plan.md)
- [v4 visualization plan tracker](../visualization-plan.md)

## EN

### Context and Problem Statement

[ADR 0002](0002-adopt-github-spec-kit-for-spec-driven-development.md) introduced GitHub Spec Kit as the primary path from requirements to implementation. It worked well for the production pipeline, source adapters, and MCP interface, where the user contract, scenarios, provenance, and privacy boundaries need to be established up front.

Later, the GraphRAG epic and v4 visualization design used detailed documentation plans with scope, dependencies, acceptance criteria, statuses, and per-PR validation. For bounded cross-cutting epics that improve an already defined system, the complete Spec Kit artifact set can duplicate such a tracker and make it harder to maintain one current plan. At the same time, informal work without a required artifact contract would reintroduce hidden decisions.

This ADR retrospectively closes the gap between ADR 0002's wording and changes already guided by the GR-0…GR-6 GraphRAG epic in the [GraphRAG plan](../graphrag-plan.md) and the selected v4 form in the [visualization plan](../visualization-plan.md). It does not portray those changes as having received prior ADR approval; it explicitly records the origin of the new rule. ADR 0002's historical decision body is not rewritten; only its status changes to `superseded` and its supersession link points here.

### Y-statement

In the context of agent-driven development of a personal knowledge base, facing both the overhead of full Spec Kit for some bounded cross-cutting epics and the risk of informal plans, we decided for a scoped hybrid workflow to preserve reviewable requirements and traceability at proportional process cost, accepting the need to classify work explicitly and maintain two standardized forms of planning artifact.

### Decision Drivers

- New user-facing capabilities and data sources need explicit WHAT/WHY, scenarios, contracts, and privacy boundaries before implementation.
- Cross-cutting remediation, research, and architecture epics often start with an agreed scope and affect several existing subsystems.
- Any lighter workflow must preserve dependencies, acceptance criteria, status, validation, and rationale for significant decisions.
- ADRs must capture architectural choices independently of the selected planning format.
- Small fixes and local refactors should not receive ceremony disproportionate to their risk.
- Agents and reviewers need an unambiguous boundary between an allowed plan tracker and an unstructured notes list.

### Considered Options

- **Spec Kit for every change.** It is uniform, but creates redundant specs/plans/tasks for local fixes and epics already described by a detailed technical tracker.
- **Free process choice without a contract.** It minimizes formalities, but leads to incomplete plans, hidden decisions, and status drift.
- **Spec Kit only for source adapters.** This is too narrow: new user interfaces and capabilities also require a feature contract.
- **Scoped hybrid workflow.** It keeps Spec Kit as the default for new user-facing features and allows a reviewable docs plan tracker for a bounded class of cross-cutting work.

### Decision Outcome

Chosen option: a scoped hybrid workflow. This ADR supersedes ADR 0002 and retains Spec Kit as the default, but not universal, workflow.

1. **Spec Kit is the default** for new user-facing capabilities, new feature/API/CLI contracts, source adapters and import workflows, and ambiguous initiatives whose requirements need clarification before implementation. The expected path is specification → plan → tasks → implementation/convergence; use clarification for ambiguities and consistency analysis before implementation.
2. **A docs plan tracker is allowed** for a bounded cross-cutting remediation, audit, research, architecture, or infrastructure epic when the owner has explicitly fixed its goal and boundaries and the work primarily modifies several existing subsystems. The reason for choosing a tracker instead of Spec Kit must be recorded in the plan or a linked ADR.
3. Such a tracker must contain: explicit scope and out-of-scope; accepted decisions and open questions; dependencies and step ordering; acceptance criteria; per-step status; planned checks and actual validation; links to related ADRs, PRs, or commits. One tracker is the epic's canonical status source.
4. A plan tracker **does not replace ADRs**. Significant decisions about data boundaries, provenance, storage, search/RAG, privacy, visualization, automation, or process are recorded as separate ADRs under the normal contract.
5. A simple defect, test or documentation fix, local refactor, or tooling maintenance does not require full Spec Kit or a separate plan tracker when it does not change an external or architectural contract. Its scope and validation must be clear from the issue/request, diff, tests, and commit/PR description.
6. Before an architectural feature is completed, its diff is reviewed for ADR-significant decisions. If a gap is discovered after implementation, a retrospective `accepted` ADR is allowed: it must identify the originating plan, PR, commit, or change set and state explicitly that it was not prior approval.

### Consequences

- Good: Spec Kit remains a predictable standard for features where requirements and user contracts matter more than a technical tracker.
- Good: bounded cross-cutting epics can use one detailed, current plan without duplicating artifacts.
- Good: the required tracker contents and independent ADR contract preserve reviewability and decision rationale.
- Bad: initiatives must be classified before work begins; incorrect classification may result in insufficient specification.
- Bad: two workflows require discipline so that a docs tracker does not degrade into an arbitrary task list.
- Neutral: existing Spec Kit artifacts remain valid project artifacts; this ADR does not migrate or delete them.
- Neutral: a retrospective ADR closes a decision-log gap but does not change the historical order in which changes were approved.

### Review Plan

Revisit the decision if agents repeatedly choose the lighter path for ambiguous user-facing features, trackers omit required fields or drift from implementation, Spec Kit becomes substantially simpler for cross-cutting epics, the Git extension is enabled, or multi-user development requires stricter unified governance. If classification errors recur, add automated checks or a decision checklist.

### Links

- [ADR 0002: adopt GitHub Spec Kit](0002-adopt-github-spec-kit-for-spec-driven-development.md)
- [AGENTS.md: Spec-Driven Development rules](../../AGENTS.md#spec-driven-development)
- [README: Spec-Driven Development](../../README.md#spec-driven-development)
- [GraphRAG plan tracker](../graphrag-plan.md)
- [v4 visualization plan tracker](../visualization-plan.md)
