# Tasks: Writer/Research Workflow

**Input**: design artifacts из `specs/007-writer-research-workflow/`

**Prerequisites**: [spec.md](spec.md), [plan.md](plan.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md), принятый [ADR 0010](../../docs/adr/0010-adopt-provenance-gated-writer-research-file-workflow.md)

**Tests**: обязательны, поскольку plan задаёт unit, contract, integration, no-mutation, path-safety и independent acceptance gates. В каждой story test tasks выполняются первыми и должны зафиксировать ожидаемый failure до implementation.

**Organization**: задачи сгруппированы по user stories, чтобы каждый инкремент имел самостоятельный проверяемый результат. Existing `retrieval.py`, CLI search и MCP read contracts не меняются; V5 использует отдельные read queries.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: можно выполнять параллельно после выполнения указанных dependencies — задача меняет другие файлы и не зависит от незавершённой соседней задачи.
- **[Story]**: `[US1]`, `[US2]` или `[US3]` связывает задачу с user story из `spec.md`.
- Каждый checklist item содержит точный путь к изменяемому файлу.

## Phase 1: Setup — contract и test infrastructure

**Purpose**: подготовить воспроизводимую test-only JSON Schema validation и безопасные fixtures без новой runtime dependency.

- [ ] T001 Добавить прямую dev-only зависимость `jsonschema>=4.26` в `pyproject.toml` и обновить `uv.lock`, сохранив base package без новых runtime dependencies
- [ ] T002 [P] Создать общий synthetic published/draft/tainted corpus без реальных excerpts в `tests/fixtures/research/safe-research-corpus.json`
- [ ] T003 [P] Добавить reusable builders только для ResearchRequest, Citation и dossier packages в `tests/conftest.py`, не включая credentials или private paths

**Checkpoint**: dev environment валидирует JSON Schema 2020-12, а shared fixtures доступны unit и integration tests.

---

## Phase 2: Foundational — domain, contracts и secure artifact I/O

**Purpose**: реализовать общие primitives, без которых нельзя безопасно начать ни одну user story.

**⚠️ CRITICAL**: все задачи этой фазы блокируют US1–US3.

### Tests first

- [ ] T004 [P] Написать failing tests для Draft 2020-12 validity всех шести schemas, Citation/DossierManifest/ValidationResult examples, canonical JSON, digests, collision detection, strict unknown/version rejection, HTTP(S)-only URL projection, directory/single-file output safety, `0700`/`0600` и atomic cleanup в `tests/unit/test_research_artifacts.py`
- [ ] T005 [P] Написать failing tests для `ResearchVisibility`, ResearchRequest bounds/date conversion, Citation/EvidenceCandidate invariants и ValidationResult states в `tests/unit/test_research_workflow.py`

### Implementation

- [ ] T006 Реализовать typed domain models и invariant validation для ResearchVisibility, ResearchRequest, Citation, EvidenceCandidate, CurationOperation, DossierRevision и ValidationResult в `src/knowledge_base/research_workflow.py`
- [ ] T007 Реализовать canonical JSON, SHA-256 projections/IDs, collision checks, safe HTTP(S) projection и reusable stdlib strict-object/version parsers в `src/knowledge_base/research_artifacts.py`
- [ ] T008 Реализовать generated-zone classification, unsafe-root acknowledgement, component-wise symlink refusal, POSIX `0700`/`0600`, immutable collision handling и same-parent atomic publication как для directory packages, так и для standalone handoff JSON в `src/knowledge_base/research_artifacts.py`

**Checkpoint**: `tests/unit/test_research_artifacts.py` и foundational cases в `tests/unit/test_research_workflow.py` проходят без ArangoDB и без runtime `jsonschema` import.

---

## Phase 3: User Story 1 — собрать исследовательское досье по теме (Priority: P1) 🎯 MVP

**Goal**: построить published-only по умолчанию immutable dossier revision с exact chunk evidence, provenance, deterministic selection и согласованными JSON/Markdown/validation projections.

**Independent Test**: на отдельном published+draft+tainted-community corpus создать dossier, доказать отсутствие влияния drafts без opt-in, разрешить каждый excerpt до chunk/provenance и получить тот же content digest/order при неизменных inputs.

### Tests first

- [ ] T009 [P] [US1] Написать failing query tests для status/source/date scope до ranking, lexical/vector exact chunk hydration, provenance ownership, related/topic leads, tainted-summary suppression и optional degradation в `tests/unit/test_research_retrieval.py`
- [ ] T010 [P] [US1] Написать failing pure tests для citation projection, deterministic fusion/tie-breaks, identity dedupe, round-robin document diversity, per-document/candidate/evidence caps и stable ordering в `tests/unit/test_research_workflow.py`
- [ ] T011 [P] [US1] Написать failing artifact tests для manifest↔Markdown selected-set parity, Unicode/control-character rendering, ready/degraded manifests, no-evidence non-publication и atomic root revision в `tests/unit/test_research_artifacts.py`
- [ ] T012 [P] [US1] Написать failing CLI tests для `kb research build`, bounds/options, published default, draft opt-in banner, JSON stdout и `ok|degraded|no_evidence|error` exit semantics в `tests/unit/test_cli.py`
- [ ] T013 [P] [US1] Создать failing end-to-end build cases с isolated published/draft corpus, raw/import provenance, related edges, tainted community и before/after collection snapshots в `tests/integration/test_research_workflow_pipeline.py`

### Implementation

- [ ] T014 [P] [US1] Реализовать отдельные V5 lexical/vector chunk candidate queries с explicit visibility/source/UTC-date scope, bounded overfetch и exact cosine re-score в `src/knowledge_base/research_retrieval.py`
- [ ] T015 [US1] Добавить exact document/chunk/raw/source hydration, visibility-filtered related/topic leads и allowlisted CorpusContext/index freshness без сериализации Settings credentials в `src/knowledge_base/research_retrieval.py`
- [ ] T016 [P] [US1] Реализовать Citation identity/provenance projection и deterministic multi-fragment selection с dedupe, diversity, caps и stable tie-breaks в `src/knowledge_base/research_workflow.py`
- [ ] T017 [US1] Реализовать dossier build orchestration, request validation, optional-context degradation и честный no-evidence outcome без DB mutations в `src/knowledge_base/research_workflow.py`
- [ ] T018 [US1] Реализовать dossier manifest, human-readable Markdown, initial validation projection и immutable root revision publication в `src/knowledge_base/research_artifacts.py`
- [ ] T019 [US1] Добавить parser/handler `kb research build` с embedding provider, output-root safety flags и contract exit codes в `src/knowledge_base/cli/main.py`

**Checkpoint**: US1 самостоятельно создаёт и валидно читает extractive dossier; legacy `kb search` и MCP остаются неизменными.

---

## Phase 4: User Story 2 — проверить, курировать и воспроизвести подборку (Priority: P2)

**Goal**: повторно проверять citations и создавать immutable child revisions через include/exclude/pin без повторного retrieval и изменения parent.

**Independent Test**: проверить root revision, получить `valid|missing|changed|hidden`, затем создать child revision тремя curation operations и доказать byte-identical parent, explicit lineage и отсутствие DB mutations.

### Tests first

- [ ] T020 [P] [US2] Написать failing tests для document/chunk ownership, normalized offsets, excerpt/provenance hashes и citation states `valid|missing|changed|hidden` в `tests/unit/test_research_workflow.py`
- [ ] T021 [US2] Написать failing tests для ordered include/exclude/pin, bounded parent candidate universe, unknown/no-op/conflicting rejection и запрета retrieval во время curation в `tests/unit/test_research_workflow.py`
- [ ] T022 [P] [US2] Написать failing tests для artifact loading/integrity, deterministic validation report, child lineage/content digest и byte-identical parent files в `tests/unit/test_research_artifacts.py`
- [ ] T023 [P] [US2] Написать failing CLI tests для `kb research validate` и `kb research curate`, repeated operation flags, current-parent gate, custom-root acknowledgement и rejection exits в `tests/unit/test_cli.py`
- [ ] T024 [P] [US2] Расширить failing integration cases repeat-build determinism, successful child revision, missing/changed/hidden parent rejection и before/after content snapshots в `tests/integration/test_research_workflow_pipeline.py`

### Implementation

- [ ] T025 [US2] Реализовать strict dossier directory loading, manifest/schema/version parsing и file digest/integrity checks до обращения к corpus в `src/knowledge_base/research_artifacts.py`
- [ ] T026 [US2] Реализовать citation revalidation загруженного dossier против current documents/chunks/provenance со всеми четырьмя states в `src/knowledge_base/research_workflow.py`
- [ ] T027 [US2] Реализовать read-only dossier validation service и deterministic aggregate ValidationResult без repair/rewrite target revision в `src/knowledge_base/research_workflow.py`
- [ ] T028 [US2] Реализовать include/exclude/pin state transitions, ordered operation log, parent validation gate и child revision construction без retrieval в `src/knowledge_base/research_workflow.py`
- [ ] T029 [US2] Реализовать child manifest/Markdown/validation rendering и immutable publication поверх уже проверенного parent в `src/knowledge_base/research_artifacts.py`
- [ ] T030 [US2] Добавить dossier dispatch для `kb research validate` и handler `kb research curate` с contract exit/warning semantics в `src/knowledge_base/cli/main.py`

**Checkpoint**: US2 воспроизводимо проверяет и курирует US1 artifacts, не изменяя parent или ArangoDB.

---

## Phase 5: User Story 3 — подготовить citation-aware draft или summary (Priority: P3)

**Goal**: создать acknowledged handoff, принять untrusted `draft|summary` package, проверить identity/citations/coverage и атомарно сохранить generated writing artifact.

**Independent Test**: отдельно выполнить draft и summary round-trips; оба должны пройти один contract, а wrong kind, unknown citation, changed evidence, bad digest, missing acknowledgements и path/URL instructions должны отклоняться без valid artifact.

### Tests first

- [ ] T031 [US3] Создать writing-output fixtures `tests/fixtures/research/valid-writing-output-draft.json`, `tests/fixtures/research/valid-writing-output-summary.json`, `tests/fixtures/research/invalid-writing-output.json` и добавить handoff/writing builders в `tests/conftest.py`
- [ ] T032 [US3] Написать failing schema+runtime-parser tests для writing-output fixtures и strict size/version/unknown-field bounds в `tests/unit/test_writing_handoff.py`
- [ ] T033 [US3] Написать failing schema+runtime tests для HandoffPackage и non-circular identity, idempotent reuse, evidence allowlist, every-handoff egress acknowledgement, second draft acknowledgement, standalone `0600`, symlink/custom-root rejection и atomic cleanup в `tests/unit/test_writing_handoff.py`
- [ ] T034 [US3] Написать failing tests для `draft|summary` symmetry, cross-kind rejection, content/package digests, exhaustive section ranges, allowlisted citations, unsupported reasons, handoff/imported-artifact validation и запрета чтения package paths/URLs в `tests/unit/test_writing_handoff.py`
- [ ] T035 [P] [US3] Написать failing schema+runtime tests для ImportedWritingManifest, inherited acknowledgements/warnings, `human_reviewed=false`, atomic/idempotent output publication и path permissions в `tests/unit/test_research_artifacts.py`
- [ ] T036 [P] [US3] Написать failing CLI tests для `kb research handoff`, `kb research import-output` и `kb research validate --handoff`, обоих output kinds, acknowledgement truth table, stderr warnings и stable JSON exit codes в `tests/unit/test_cli.py`
- [ ] T037 [P] [US3] Добавить failing integration round-trips для draft и summary, validators всех artifact types, identical re-import, wrong-kind/unknown-citation/changed-evidence rejection и DB content snapshots в `tests/integration/test_research_workflow_pipeline.py`

### Implementation

- [ ] T038 [P] [US3] Реализовать handoff projection, non-circular identity/package digests, citation allowlist, quoted-data instructions, egress/draft gates и secure atomic standalone-file reuse через `research_artifacts.py` в `src/knowledge_base/writing_handoff.py`
- [ ] T039 [US3] Реализовать bounded manual parser и structural validator writing-output package для обоих kinds, digest/identity/visibility/coverage/citation/current-dossier checks в `src/knowledge_base/writing_handoff.py`
- [ ] T040 [P] [US3] Реализовать imported-writing manifest/output/validation rendering, content-derived `writing_id` и atomic idempotent publication в `src/knowledge_base/research_artifacts.py`
- [ ] T041 [US3] Реализовать import orchestration и service-level validators для handoff, incoming writing-output с explicit handoff и imported-writing artifacts, включая acknowledgement propagation и whole-package rejection, в `src/knowledge_base/writing_handoff.py`
- [ ] T042 [US3] Добавить handlers `kb research handoff` и `kb research import-output` и полный validate dispatch с `--handoff`/`--output-root` resolution в `src/knowledge_base/cli/main.py`

**Checkpoint**: US3 независимо принимает и draft, и summary, сохраняя generated/source-of-truth boundary и read-only MCP.

---

## Phase 6: Polish, regression и acceptance gates

**Purpose**: доказать отсутствие regression/mutations, синхронизировать документацию и провести независимую приёмку всех четырёх gates.

- [ ] T043 [P] Добавить regression tests, что V5 не меняет legacy search visibility/result envelopes и MCP read-only tools, в `tests/unit/test_retrieval.py` и `tests/unit/test_mcp_service.py`
- [ ] T044 Усилить seeded isolated integration проверкой deterministic per-collection content hashes без подключения к owner corpus в `tests/integration/test_research_workflow_pipeline.py`
- [ ] T045 Выполнить отдельный opt-in real-corpus build ≤30s / validation ≤5s measurement и записать corpus identity, timings и artifact IDs только в automated evidence поля `specs/007-writer-research-workflow/acceptance.md`
- [ ] T046 [P] Добавить Markdown link checker `scripts/check-markdown-links.mjs`, команды `check:docs-links`/aggregate `check` в `package.json` и ADR/link gates в `.github/workflows/ci.yml`
- [ ] T047 [P] Обновить реализованные `kb research` commands, generated-data boundary и фактический V5 status в `README.md`, `docs/architecture.md` и `docs/roadmap.md`
- [ ] T048 [P] Перевести future-tense examples в исполнимый acceptance flow и сверить все команды/fixtures с runtime в `specs/007-writer-research-workflow/quickstart.md`
- [ ] T049 Запустить unit/integration/coverage, ruff, format, mypy, contract, ADR, link и `git diff --check` gates и записать automated evidence, не меняя independent results, в `specs/007-writer-research-workflow/acceptance.md`
- [ ] T050 Независимо выполнить dossier/citation/curation acceptance и записать reviewer, artifact IDs, evidence и result секции 1 в `specs/007-writer-research-workflow/acceptance.md`
- [ ] T051 Независимо выполнить draft round-trip acceptance и записать reviewer, handoff/package/writing IDs, evidence и result секции 2 в `specs/007-writer-research-workflow/acceptance.md`
- [ ] T052 Независимо выполнить summary round-trip acceptance, включая cross-kind negative case, и записать evidence/result секции 3 в `specs/007-writer-research-workflow/acceptance.md`
- [ ] T053 Независимо выполнить privacy/path-safety acceptance, затем при четырёх PASS заполнить итоговое решение секции 4 в `specs/007-writer-research-workflow/acceptance.md`

**Checkpoint**: Feature 007 завершена только после T045+T049 и четырёх независимых PASS в T050–T053.

---

## Dependencies & Execution Order

### Phase dependencies

```text
Phase 1 Setup
  └─> Phase 2 Foundation
        └─> Phase 3 US1 dossier (MVP)
              └─> Phase 4 US2 validation/curation
                    └─> Phase 5 US3 handoff + draft/summary
                          └─> Phase 6 regression/docs/acceptance
```

- **Phase 1**: стартует сразу; T002 и T003 параллельны T001.
- **Phase 2**: зависит от T001–T003; tests T004/T005 выполняются и падают до T006–T008.
- **US1**: зависит от T004–T008; T009–T013 пишутся до implementation. T014 и T016 можно вести параллельно; T015 зависит от T014; T017 зависит от T015+T016; T018 зависит от T016; T019 зависит от T017+T018.
- **US2**: зависит от US1 checkpoint. T020–T024 пишутся до implementation; T025 loader должен завершиться до T026, затем T026 → T027 → T028; T029 зависит от T028, T030 зависит от T027–T029.
- **US3**: зависит от US2 validation gate. T031 создаёт только US3 fixtures/builders, затем T032–T037 пишутся до implementation; T038 и T040 можно вести параллельно; T039 зависит от T038, T041 зависит от T039+T040, T042 зависит от T041.
- **Phase 6**: T043, T046, T047 и T048 можно вести параллельно после US3; T044 ждёт полный seeded pipeline; opt-in T045 выполняется отдельно от tests; T049 ждёт T043–T048; T050–T053 выполняются после T049 независимым reviewer.

### User story dependencies

- **US1 (P1)**: после Foundation, без других story dependencies; это MVP.
- **US2 (P2)**: использует immutable dossier из US1, но имеет собственный independent validation/curation test.
- **US3 (P3)**: использует validated revision из US2; draft и summary обязаны проходить отдельные independent cases.

### Contract mapping

- `citation.schema.json` и `dossier-manifest.schema.json` → Foundation, US1, US2.
- `validation-result.schema.json` → Foundation, US1, US2, US3 validate dispatch.
- `handoff-package.schema.json` → US3 handoff creation.
- `writing-output-package.schema.json` и `imported-writing-manifest.schema.json` → US3 import and publication.
- `contracts/cli.md` → T012/T019, T023/T030, T036/T042.

## Parallel execution examples

### US1

```text
Parallel tests: T009 research retrieval | T010 selection | T011 artifacts | T012 CLI | T013 integration
Parallel implementation after Foundation: T014 retrieval core | T016 pure selection
```

### US2

```text
Parallel tests: T020 citation validation | T022 artifact lineage | T023 CLI | T024 integration
Sequential implementation boundary: T025 strict loader → T026 corpus revalidation; child publication T029 follows T028 curation construction
```

### US3

```text
After US3 fixture setup T031 and sequential contract tests T032–T034:
Parallel tests: T035 imported artifact | T036 CLI | T037 integration
Parallel implementation: T038 handoff builder | T040 imported artifact publisher
```

## Implementation Strategy

### MVP first

1. Выполнить T001–T008.
2. Выполнить T009–T019 для US1.
3. Остановиться и независимо проверить published-only dossier, citation provenance, determinism и no-mutation.
4. Не называть MVP завершённой Feature 007: обязательные US2, US3 и independent acceptance ещё впереди.

### Incremental delivery

1. **Foundation** → safe local artifact and contract primitives.
2. **US1** → extractive dossier MVP, пригодный без writing-agent.
3. **US2** → verified immutable curation lineage.
4. **US3** → acknowledged file handoff и symmetric draft/summary import.
5. **Polish/acceptance** → regression, performance, docs и четыре независимых PASS.

### Commit discipline

- Коммитить tests отдельно до implementation каждого логического среза, сохраняя наблюдаемый expected failure.
- Коммитить implementation малыми dependency-ordered группами по модулям `research_artifacts.py`, `research_retrieval.py`, `research_workflow.py`, `writing_handoff.py`, затем CLI.
- Не смешивать generated runtime artifacts из `data/generated/` с project artifacts в `specs/` и `docs/`.
- Не выставлять `human_reviewed=true` и не менять independent acceptance results автоматически.
