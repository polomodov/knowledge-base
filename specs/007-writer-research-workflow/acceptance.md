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
| Automated gates evidence | TBD |
| Итоговый result | NOT RUN |

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
