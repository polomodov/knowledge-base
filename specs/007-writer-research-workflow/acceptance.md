# Independent Acceptance Gate: Writer/Research Workflow

Этот документ — записываемый независимый gate Feature 007. Его заполняет reviewer, который не выполнял проверяемую реализацию. Automated tests являются prerequisite, но не заменяют отдельную приёмку dossier/citations/curation, обоих `output_kind` и privacy/path-safety boundary.

До implementation и фактического запуска все результаты намеренно остаются `NOT RUN`. Допустимые значения: `PASS`, `FAIL`, `BLOCKED`, `NOT RUN`. Feature 007 принимается только при `PASS` во всех четырёх секциях и в итоговом решении.

## Общая запись запуска

| Поле | Значение |
|------|----------|
| Reviewer | TBD |
| Дата и время (UTC) | TBD |
| Implementation commit | TBD |
| Test corpus / fixture identity | TBD |
| Automated gates evidence | PASS — см. раздел «Automated evidence (T045/T049)» ниже |
| Итоговый result | NOT RUN |

## Automated evidence (T045/T049)

Этот раздел фиксирует только машинные проверки и не является independent acceptance. Он не меняет `NOT RUN` в секциях 1–4, не выставляет `human_reviewed=true` и не подтверждает factual correctness или secret-free exact excerpts.

| Поле | Значение |
|------|----------|
| Дата запуска (UTC) | 2026-07-13 |
| Проверенный implementation commit | `80c73f2b3af0baca03558fa743128a7c378fe316` |
| Real corpus identity | DB `knowledge_base`; 3 sources, 2 972 documents, 24 877 chunks, 422 topics, 2 authors, 0 works, 11 communities; effective artifact embedding model `hash-v1`, 8 dimensions |
| Real-corpus build | Два отдельных published-only запуска сразу после полного CI-parity suite: `rev-20260713T234617Z-2344dd8f` за 5.441 s и `rev-20260713T234618Z-9d554152` за 1.808 s; оба `status=ok`, 36 candidates / 14 evidence, `dossier_key=research-topic-d003992b38b6`, одинаковые candidate/selected order и `content_digest=b5be84c701c3065b2016a66113998b57dbe0487bc77f8b9b1be9bb1c5f125a8d` |
| Real-corpus validation | `rev-20260713T234618Z-9d554152`: `status=valid`, 14/14 citations valid, 0.229 s |
| Artifact sizes | `manifest.json` 107 505 bytes; `dossier.md` 9 695 bytes; `validation.json` 1 982 bytes; revision directory `0700`, files `0600` |
| Automated privacy projection | В manifest не найдены структурные `password`, `credentials`, `cookie`, `raw_payload`, archive/file/local/corpus path, provider key или token fields; owner DB aggregate до/после совпал. Unstructured excerpts намеренно не объявляются автоматически очищенными и остаются предметом owner review |
| Executable structural smoke | Isolated V5 suite выполнил immutable child, оба `draft|summary` round-trip, idempotent re-import и whole-package rejection для unknown-citation/cross-kind/changed-evidence cases |
| Full pytest + coverage | `564 passed, 1 skipped`; total branch coverage 83.94%; unit и live-Arango integration вместе, 55.67 s |
| Isolated V5 integration | 10 passed за 22.84 s; UUID test DB; canonical SHA-256 и counts всех document/edge collections совпали до и после полного pipeline |
| Contract gates | 6 focused Draft 2020-12/schema contract checks passed; полный suite также проверил runtime strict parsers и fixtures |
| Static/docs/build gates | Ruff check PASS; Ruff format 84 files PASS; mypy 43 source files PASS; ADR check 11 PASS; Markdown links 361/88 PASS; visualization template PASS; wheel resource PASS; base package imports without MCP extra; `git diff --check` PASS |
| Независимая приёмка | T050–T053 не запускались; секции 1–4 и итоговое решение остаются `NOT RUN` |

Первый pre-fix диагностический запуск на `ddb6211` после тяжёлого integration suite получил client timeout: ArangoDB завершила lexical query за 16.43 s, но transport budget был жёстко ограничен 10 s, и валидный artifact не публиковался. В `80c73f2` обычный HTTP budget оставлен 10 s, а bounded AQL/cursor budget приведён к 30 s, совместимым с V5 performance gate. Оба зачётных build выше намеренно запускались сразу после полного suite, уложились в лимит 30 s и подтвердили одинаковый content digest и порядок; validation уложилась в лимит 5 s.

## 1. Dossier, citations и curation

Reviewer независимо проверяет:

1. Published-only build создаёт согласованные `manifest.json`, `dossier.md`, `validation.json` и не включает drafts.
2. Каждая selected citation разрешается до source/document/chunk, exact excerpt/hash, offsets и доступного provenance.
3. Повторный build на неизменных inputs даёт новый `revision_id`, но тот же `content_digest` и порядок evidence.
4. Include/exclude/pin создают child revision с ordered operations и `parent_revision_id`; parent остаётся byte-identical.
5. Missing/changed/hidden citation и stale parent дают явный rejection либо invalid validation result без валидного child/handoff.
6. Build, validate и curate не изменяют canonical, raw, processed или derived records.

| Поле | Значение |
|------|----------|
| Reviewer | TBD |
| Дата (UTC) | TBD |
| `dossier_key` | TBD |
| Root `revision_id` / `content_digest` | TBD |
| Child `revision_id` / `content_digest` | TBD |
| Citation IDs sampled | TBD |
| Validation artifact/log IDs | TBD |
| Result | NOT RUN |
| Notes / deviations | TBD |

## 2. Draft round-trip

Reviewer создаёт handoff с `output_kind=draft` и обязательным `--acknowledge-external-disclosure`, получает независимый structured writing-output package и импортирует его через `kb research import-output`.

Проверяется совпадение output kind, handoff/dossier identities и digests; citation allowlist; полное section-range coverage; явная маркировка unsupported sections; immutable generated output; `human_reviewed=false`; отсутствие factual-verification claim. Wrong kind, unknown citation, changed evidence и mismatched handoff должны отклоняться целиком.

| Поле | Значение |
|------|----------|
| Reviewer | TBD |
| Дата (UTC) | TBD |
| `dossier_key` / `revision_id` | TBD |
| Draft `handoff_id` / digest | TBD |
| Input package ID / digest | TBD |
| Imported `writing_id` | TBD |
| Validation artifact/log IDs | TBD |
| Result | NOT RUN |
| Notes / deviations | TBD |

## 3. Summary round-trip

Reviewer повторяет независимый flow с `output_kind=summary`: отдельный handoff с `--acknowledge-external-disclosure`, writing-output package и `kb research import-output`. Summary должна пройти тот же schema, integrity, identity, citation и structural-coverage contract, но сохранить `output_kind=summary` в imported manifest.

Проверяются positive import и negative cross-kind case: summary package нельзя импортировать против draft handoff, и наоборот.

| Поле | Значение |
|------|----------|
| Reviewer | TBD |
| Дата (UTC) | TBD |
| `dossier_key` / `revision_id` | TBD |
| Summary `handoff_id` / digest | TBD |
| Input package ID / digest | TBD |
| Imported `writing_id` | TBD |
| Validation artifact/log IDs | TBD |
| Result | NOT RUN |
| Notes / deviations | TBD |

## 4. Privacy и path safety

Reviewer независимо подтверждает все границы:

1. Любой handoff без `--acknowledge-external-disclosure` отклоняется, включая published-only dossier.
2. Handoff с draft evidence требует одновременно `--acknowledge-external-disclosure` и `--allow-draft-evidence`; оба acknowledgement записаны downstream.
3. Handoff исключает структурные raw payload, archive/file paths, cookies и credentials; exact excerpts явно рассматриваются как потенциально sensitive и проходят owner review без ложного обещания automatic secret redaction.
4. Default package directories/files создаются owner-only (`0700`/`0600` на поддерживаемой POSIX-платформе).
5. Output root вне `data/generated/` без `--acknowledge-unsafe-output` отклоняется; с флагом создаётся artifact и записывается prominent unsafe-location warning.
6. Symlink root или symlink component отклоняется и для default, и для custom output независимо от acknowledgement flags.
7. Incoming package paths/URLs не читаются и не выполняются.

| Поле | Значение |
|------|----------|
| Reviewer | TBD |
| Дата (UTC) | TBD |
| Published handoff ID | TBD |
| Draft-inclusive handoff ID | TBD |
| Custom-root artifact IDs | TBD |
| Permission / symlink test evidence | TBD |
| Warning/error codes observed | TBD |
| Result | NOT RUN |
| Notes / deviations | TBD |

## Итоговое решение

| Gate | Result |
|------|--------|
| Dossier, citations и curation | NOT RUN |
| Draft round-trip | NOT RUN |
| Summary round-trip | NOT RUN |
| Privacy и path safety | NOT RUN |
| **Feature 007 acceptance** | **NOT RUN** |

Итоговый reviewer после четырёх `PASS` записывает ниже решение и ссылки на сохранённые evidence/log artifacts. Любой `FAIL`, `BLOCKED` или `NOT RUN` означает, что Feature 007 ещё не принята.

- Final reviewer: TBD
- Decision date (UTC): TBD
- Decision evidence: TBD
- Deviations accepted by owner: TBD
