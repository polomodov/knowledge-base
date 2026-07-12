# 0011. Зафиксировать границу MCP и research CLI и SSOT processed в ArangoDB / Clarify MCP vs research CLI boundary and processed SSOT in ArangoDB

```json adr-meta
{
  "id": "0011",
  "titleRu": "Зафиксировать границу MCP и research CLI и SSOT processed в ArangoDB",
  "titleEn": "Clarify MCP vs research CLI boundary and processed SSOT in ArangoDB",
  "status": "accepted",
  "date": "2026-07-13",
  "deciders": [
    "knowledge-base maintainer"
  ],
  "tags": [
    "mcp",
    "research",
    "storage",
    "processed-data",
    "architecture"
  ],
  "supersedes": [],
  "supersededBy": []
}
```

## RU

### Контекст и проблема

После принятия read-only MCP ([ADR 0004](0004-local-read-only-mcp-server-for-knowledge-base.md)), provenance-границ raw/processed/generated ([ADR 0005](0005-define-source-provenance-and-private-archive-boundaries.md)) и file-first writer/research workflow ([ADR 0010](0010-adopt-provenance-gated-writer-research-file-workflow.md)) оставались два уточнения статуса:

1. Можно ли расширить MCP write- или dossier-build tools, чтобы агенты собирали research packages без CLI.
2. Является ли `data/processed/` ожидаемой on-disk зоной нормализованных данных или processed SSOT уже живёт в ArangoDB.

Без явной записи эти границы легко размыть в follow-up фичах и в инструкциях для агентов. Этот ADR ретроспективно фиксирует default-решение remediation/docs plan (закрытие N10 и N13): он не был предварительным одобрением до реализации Feature 006/007, а закрепляет уже принятую практику и закрывает пробел в ADR-наборе.

### Y-statement

В контексте локальной knowledge-base с read-only MCP и file-first research workflow, столкнувшись с риском смешать agent write-path с dossier CLI и с неоднозначностью зоны `data/processed/`, мы решили сохранить MCP как search/document/graph/health read-only surface, а research dossier workflow — как file CLI по ADR 0010, и явно зарезервировать `data/processed/` без материализации, считая ArangoDB SSOT для processed данных, чтобы сохранить trust boundary и ясный storage contract, принимая ручной CLI round-trip для research и отсутствие on-disk processed mirror.

### Драйверы решения

- MCP v1 (ADR 0004) намеренно не мутирует корпус и не пишет generated artifacts.
- Research packages требуют validation, atomic publish и provenance gates, уже закреплённых в file CLI (ADR 0010).
- On-disk `data/raw/` / `data/processed/` / `data/generated/` остаются логическими зонами; нормализованные documents/chunks уже живут в ArangoDB (ADR 0005).
- Агентам нужен короткий, однозначный контракт: где читать, где писать, что out-of-scope.

### Рассмотренные варианты

1. **Один ADR: MCP research boundary + processed SSOT clarification (выбран).** Закрывает оба follow-up одним docs-only решением без supersede ADR 0004/0005/0010.
2. **Два отдельных ADR.** Чище по теме, но избыточно для двух коротких уточнений одного remediation wave.
3. **Добавить MCP write / dossier-build tools сейчас.** Ускоряет agent UX, но расширяет mutation surface, дублирует CLI validation path и нарушает принятый read-only MCP contract.
4. **Материализовать `data/processed/` как on-disk SSOT рядом с ArangoDB.** Удобно для offline dump, но создаёт второй источник истины, sync-долг и риск drift.

### Итоговое решение

Выбран вариант 1.

**MCP vs research:**

- MCP остаётся локальным stdio read-only интерфейсом для search, document, graph neighbors, source inventory и health.
- Research dossier / handoff / import-output workflow остаётся file CLI (`kb research …`) по ADR 0010.
- Явно out-of-scope сейчас: MCP write tools, MCP dossier build/curate/handoff/import tools и любой agent path, который публикует research artifacts в обход CLI validation.

**Processed zone / SSOT:**

- Каталог `data/processed/` зарезервирован как логическая on-disk зона для возможной будущей материализации нормализованных артефактов.
- Текущий processed SSOT — ArangoDB (documents, chunks, derived indexes и связанный provenance graph).
- `data/processed/` в v1 не материализуется и не является вторым источником истины; exports и research packages пишутся в `data/generated/`.
- Это уточнение follow-up к ADR 0005, а не supersession: raw/processed/generated границы сохраняются, меняется только явная фиксация runtime SSOT.

ADR не supersede ADR 0004, 0005 или 0010.

### Последствия

- Хорошо: агенты и contributors получают одну запись «MCP = read; research = file CLI; processed SSOT = ArangoDB».
- Хорошо: mutation/trust boundary MCP не расширяется без нового ADR.
- Плохо: writing-agent по-прежнему зависит от ручного file round-trip через CLI.
- Плохо: нет удобного on-disk processed dump без отдельного export/решения.
- Нейтрально: будущая материализация `data/processed/` или MCP research tools требуют нового ADR и не могут считаться implied этим решением.

### План пересмотра

Пересмотреть, если:

- появляется устойчивый спрос на MCP-mediated dossier build с той же validation/atomicity моделью, что CLI;
- нужен reproducible on-disk processed mirror или offline processed package как peer SSOT;
- меняется storage topology так, что ArangoDB перестаёт быть единственным runtime store для normalized data.

### Ссылки

- [ADR 0004: Local read-only MCP](0004-local-read-only-mcp-server-for-knowledge-base.md)
- [ADR 0005: Source provenance and private archive boundaries](0005-define-source-provenance-and-private-archive-boundaries.md)
- [ADR 0010: Provenance-gated writer/research file workflow](0010-adopt-provenance-gated-writer-research-file-workflow.md)
- [docs/architecture.md](../architecture.md)
- Remediation docs plan items N10 (MCP research boundary) и N13 (processed zone / ArangoDB SSOT)

## EN

### Context and Problem Statement

After accepting read-only MCP ([ADR 0004](0004-local-read-only-mcp-server-for-knowledge-base.md)), raw/processed/generated provenance boundaries ([ADR 0005](0005-define-source-provenance-and-private-archive-boundaries.md)), and the file-first writer/research workflow ([ADR 0010](0010-adopt-provenance-gated-writer-research-file-workflow.md)), two status clarifications remained:

1. Whether MCP should gain write or dossier-build tools so agents can assemble research packages without the CLI.
2. Whether `data/processed/` is the expected on-disk home for normalized data, or whether processed SSOT already lives in ArangoDB.

Without an explicit record, follow-up features and agent instructions can blur these boundaries. This ADR retrospectively records the remediation/docs plan default (closing N10 and N13): it was not prior approval before Feature 006/007 landed; it documents established practice and closes a gap in the ADR set.

### Y-statement

In the context of a local knowledge base with read-only MCP and a file-first research workflow, facing the risk of mixing an agent write-path with the dossier CLI and ambiguity about the `data/processed/` zone, we decided to keep MCP as a search/document/graph/health read-only surface, keep the research dossier workflow as the file CLI per ADR 0010, and explicitly reserve `data/processed/` without materializing it while treating ArangoDB as the processed SSOT, to preserve the trust boundary and a clear storage contract, accepting a manual CLI research round-trip and no on-disk processed mirror.

### Decision Drivers

- MCP v1 (ADR 0004) intentionally does not mutate the corpus or write generated artifacts.
- Research packages need validation, atomic publish, and provenance gates already fixed in the file CLI (ADR 0010).
- On-disk `data/raw/` / `data/processed/` / `data/generated/` remain logical zones; normalized documents/chunks already live in ArangoDB (ADR 0005).
- Agents need a short, unambiguous contract: where to read, where to write, and what is out of scope.

### Considered Options

1. **One ADR: MCP research boundary + processed SSOT clarification (chosen).** Closes both follow-ups in a single docs-only decision without superseding ADR 0004/0005/0010.
2. **Two separate ADRs.** Cleaner by topic, but excessive for two short clarifications in one remediation wave.
3. **Add MCP write / dossier-build tools now.** Improves agent UX, but widens the mutation surface, duplicates the CLI validation path, and breaks the accepted read-only MCP contract.
4. **Materialize `data/processed/` as an on-disk SSOT beside ArangoDB.** Convenient for offline dumps, but creates a second source of truth, sync debt, and drift risk.

### Decision Outcome

Chosen option: 1.

**MCP vs research:**

- MCP remains a local stdio read-only interface for search, document, graph neighbors, source inventory, and health.
- The research dossier / handoff / import-output workflow remains the file CLI (`kb research …`) per ADR 0010.
- Explicitly out of scope for now: MCP write tools, MCP dossier build/curate/handoff/import tools, and any agent path that publishes research artifacts bypassing CLI validation.

**Processed zone / SSOT:**

- The `data/processed/` directory is reserved as a logical on-disk zone for a possible future materialization of normalized artifacts.
- The current processed SSOT is ArangoDB (documents, chunks, derived indexes, and the related provenance graph).
- `data/processed/` is not materialized in v1 and is not a second source of truth; exports and research packages write to `data/generated/`.
- This is a follow-up clarification to ADR 0005, not a supersession: raw/processed/generated boundaries remain; only the runtime SSOT is stated explicitly.

This ADR does not supersede ADR 0004, 0005, or 0010.

### Consequences

- Good: agents and contributors get one record: “MCP = read; research = file CLI; processed SSOT = ArangoDB.”
- Good: the MCP mutation/trust boundary does not expand without a new ADR.
- Bad: writing agents still depend on a manual file round-trip through the CLI.
- Bad: there is no convenient on-disk processed dump without a separate export/decision.
- Neutral: future materialization of `data/processed/` or MCP research tools needs a new ADR and must not be treated as implied by this decision.

### Review Plan

Revisit if:

- there is sustained demand for MCP-mediated dossier build with the same validation/atomicity model as the CLI;
- a reproducible on-disk processed mirror or offline processed package is required as a peer SSOT;
- storage topology changes so ArangoDB is no longer the sole runtime store for normalized data.

### Links

- [ADR 0004: Local read-only MCP](0004-local-read-only-mcp-server-for-knowledge-base.md)
- [ADR 0005: Source provenance and private archive boundaries](0005-define-source-provenance-and-private-archive-boundaries.md)
- [ADR 0010: Provenance-gated writer/research file workflow](0010-adopt-provenance-gated-writer-research-file-workflow.md)
- [docs/architecture.md](../architecture.md)
- Remediation docs plan items N10 (MCP research boundary) and N13 (processed zone / ArangoDB SSOT)
