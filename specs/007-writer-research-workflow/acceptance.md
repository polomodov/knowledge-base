# Independent Acceptance Gate: Writer/Research Workflow

Этот документ — записываемый независимый gate Feature 007. Его заполняет reviewer, который не выполнял проверяемую реализацию. Automated tests являются prerequisite, но не заменяют отдельную приёмку dossier/citations/curation, обоих `output_kind` и privacy/path-safety boundary.

До implementation и фактического запуска результаты намеренно оставались `NOT RUN`. Допустимые значения: `PASS`, `FAIL`, `BLOCKED`, `NOT RUN`. 14 июля 2026 года независимые reviewers выполнили все четыре секции на isolated safe fixture; Feature 007 принята после четырёх `PASS` и отдельного peer audit сохранённых evidence.

## Общая запись запуска

| Поле | Значение |
|------|----------|
| Reviewer | `Codex independent reviewer /root/review_t050` (T050); `Codex independent reviewer /root/acceptance_reviewer` (T051–T053); peer audit всех gates — `/root/review_t050` |
| Дата и время (UTC) | `2026-07-14T00:21:25Z` |
| Runtime implementation commit | `80c73f2b3af0baca03558fa743128a7c378fe316`; acceptance harness recorded HEAD `1f751b5ad1b225561b8bdd603c701920b124505c`, whose only subsequent delta was the automated-evidence documentation refresh |
| Test corpus / fixture identity | `safe-research-corpus-v1`; `tests/fixtures/research/safe-research-corpus.json`; SHA-256 `5fc90728e950b6e3cbe63a7b8981809625bf601a980a8ac5bfbb3d61b2b86d22` |
| Automated gates evidence | PASS — см. раздел «Automated evidence (T045/T049)» ниже |
| Итоговый result | PASS |

## Automated evidence (T045/T049)

Этот раздел фиксирует только машинные проверки и не является independent acceptance. На момент automated run он не менял тогдашний `NOT RUN` в секциях 1–4, не выставлял `human_reviewed=true` и не подтверждал factual correctness или secret-free exact excerpts; последующие `PASS` записаны независимыми reviewers ниже.

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
| Независимая приёмка | На момент automated run T050–T053 ещё не запускались. Последующий независимый прогон завершился четырьмя `PASS`; фактические IDs, evidence и peer verdict записаны в секциях 1–4 ниже |

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
| Reviewer | `Codex independent reviewer /root/review_t050` |
| Дата (UTC) | `2026-07-14T00:03:44Z` |
| `dossier_key` | `research-grounded-graph-context-evidence-telescope-notes-1315d4e605f8` |
| Root `revision_id` / `content_digest` | `rev-20260714T000337Z-b6b582cb` / `7f780f49ad3148fc0ecd3cb03518d4f52e4551e5340ebee2031b4cd90d431338` |
| Child `revision_id` / `content_digest` | `rev-20260714T000338Z-0470d057` / `fab030c7b96e3f074392766ecc1dd938cf03fc006bb479cd73b3367e5e0a0d87` |
| Citation IDs sampled | `cit-e5da6427c9c217ab`, `cit-2b831e8d98ac8f17` |
| Validation artifact/log IDs | `data/generated/research/acceptance/t050-20260713T235522Z-98d6972e/evidence.json`; SHA-256 `9f7995f04a3f903acc3153de81a9f6b6c423709b6e15c10e7d111d615e21458b` |
| Result | PASS |
| Notes / deviations | Два published-only build дали разные revision IDs при одинаковых content digest, candidate order и selected order. Manifest↔Markdown parity, exact offsets/hash/provenance и ordered include/exclude/pin lineage подтверждены; parent остался byte-identical. Missing/changed/hidden validation, stale curation и stale handoff были отклонены без новых artifacts; fixture mutations восстановлены. Все проверяемые CLI-команды сохранили content hashes 18 коллекций; isolated UUID DB удалена. Отклонений нет. |

## 2. Draft round-trip

Reviewer создаёт handoff с `output_kind=draft` и обязательным `--acknowledge-external-disclosure`, получает независимый structured writing-output package и импортирует его через `kb research import-output`.

Проверяется совпадение output kind, handoff/dossier identities и digests; citation allowlist; полное section-range coverage; явная маркировка unsupported sections; immutable generated output; `human_reviewed=false`; отсутствие factual-verification claim. Wrong kind, unknown citation, changed evidence и mismatched handoff должны отклоняться целиком.

| Поле | Значение |
|------|----------|
| Reviewer | `Codex independent reviewer /root/acceptance_reviewer` |
| Дата (UTC) | `2026-07-14T00:14:45Z` |
| `dossier_key` / `revision_id` | `research-grounded-graph-context-evidence-telescope-notes-fa6dd876a7d0` / `rev-20260714T001445Z-f6e12ccc` (`content_digest=593fcfc42f9c38f14b413aaf0b742337637be0c43e09143c2b2043843866bab7`) |
| Draft `handoff_id` / digest | `handoff-92ee9436594b2cae` / `4b4d1c319d3cd4c73c2b67844d939d65b139f4b45f6cff56cbed2e487072580f` |
| Input package ID / digest | `writing-output-draft.json` (contract не задаёт отдельный package ID) / `563496d38be9ec89c8eb9eb8ba1834a4a78b6f261ca4847a10131c448f29e92e` |
| Imported `writing_id` | `writing-271b0b08809d7699` |
| Validation artifact/log IDs | `data/generated/research/acceptance/t051-t052-20260714T000154Z/evidence.json`; SHA-256 `00fb07d13655df9f5d8c4d866e79484d5ace308f5f080edc3bf9ef7dec1f5073` |
| Result | PASS |
| Notes / deviations | Отдельный stdlib-only generator запущен через `python3 -I -S` и получил только handoff JSON. Handoff, incoming package и imported artifact прошли validation; identities, digests, kind, citation allowlist, непрерывное section coverage и explicit unsupported section совпали. Re-import byte-identical и idempotent; generated boundary сохранён, `human_reviewed=false`, factual-verification claim отсутствует. Wrong kind, unknown citation, changed evidence и same-kind mismatched handoff отклонены целиком. Отклонений нет. |

## 3. Summary round-trip

Reviewer повторяет независимый flow с `output_kind=summary`: отдельный handoff с `--acknowledge-external-disclosure`, writing-output package и `kb research import-output`. Summary должна пройти тот же schema, integrity, identity, citation и structural-coverage contract, но сохранить `output_kind=summary` в imported manifest.

Проверяются positive import и negative cross-kind case: summary package нельзя импортировать против draft handoff, и наоборот.

| Поле | Значение |
|------|----------|
| Reviewer | `Codex independent reviewer /root/acceptance_reviewer` |
| Дата (UTC) | `2026-07-14T00:14:45Z` |
| `dossier_key` / `revision_id` | `research-grounded-graph-context-evidence-telescope-notes-fa6dd876a7d0` / `rev-20260714T001445Z-f6e12ccc` (`content_digest=593fcfc42f9c38f14b413aaf0b742337637be0c43e09143c2b2043843866bab7`) |
| Summary `handoff_id` / digest | `handoff-0448b98848e46f41` / `4f73d0a8a0df1ad58382df2264a245d15d1e5157dad6a58bed39a4a6f6d36b15` |
| Input package ID / digest | `writing-output-summary.json` (contract не задаёт отдельный package ID) / `e58e83a5187709eebfdc7f9c27d47d333054c833e86e158e982d2dcc9ef5adc8` |
| Imported `writing_id` | `writing-44f1017a01f93927` |
| Validation artifact/log IDs | `data/generated/research/acceptance/t051-t052-20260714T000154Z/evidence.json`; SHA-256 `00fb07d13655df9f5d8c4d866e79484d5ace308f5f080edc3bf9ef7dec1f5073` |
| Result | PASS |
| Notes / deviations | Summary прошла тот же identity, digest, allowlist, coverage, unsupported-section, immutable publication и `human_reviewed=false` contract, сохранив `output_kind=summary`. Оба cross-kind направления — draft package против summary handoff и summary package против draft handoff — отклонены целиком без изменения artifact tree. Отклонений нет. |

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
| Reviewer | `Codex independent reviewer /root/acceptance_reviewer` |
| Дата (UTC) | `2026-07-14T00:18:54Z` |
| Published handoff ID | `handoff-92ee9436594b2cae` (published-only positive flow T051); published handoff без egress acknowledgement отдельно отклонён |
| Draft-inclusive handoff ID | `handoff-3c9874eb96a6e1d5`; downstream `writing-e802f8d9e92bee93` |
| Custom-root artifact IDs | `rev-20260714T001854Z-73fab6ad` |
| Permission / symlink test evidence | `data/generated/research/acceptance/t053-20260713T234941Z/evidence.json`; SHA-256 `7a725ed0811f74c0c16cd51d2226b12d405fb4a3148b6e6470812f89798348b5` |
| Warning/error codes observed | `external_disclosure_not_acknowledged`, `draft_evidence_not_acknowledged`, `exact_evidence_requires_owner_review`, `OutputRootAcknowledgementRequired`, `output_outside_generated_zone`, `UnsafeArtifactPathError` |
| Result | PASS |
| Notes / deviations | Published handoff требует egress acknowledgement; draft-inclusive truth table принимает только оба acknowledgement, и оба флага наследуются downstream. Forbidden structured fields отсутствуют; exact excerpts явно требуют owner review. Проверены owner-only `0700`/`0600`, custom root без/с acknowledgement и default/custom intermediate symlink rejection. 13 public CLI subprocess-команд сохранили DB snapshots; отдельный guarded validate/import supplement перехватил raw/Path/normalized/file-URI, URL и shell APIs с counters `0/0/0`. Loopback URL не получил соединений, file sentinel не читался, shell marker не создавался. UUID DB удалена, а последующий database-list подтвердил её отсутствие. Отклонений нет. |

## Итоговое решение

| Gate | Result |
|------|--------|
| Dossier, citations и curation | PASS |
| Draft round-trip | PASS |
| Summary round-trip | PASS |
| Privacy и path safety | PASS |
| **Feature 007 acceptance** | **PASS** |

Итоговый reviewer после четырёх `PASS` записывает ниже решение и ссылки на сохранённые evidence/log artifacts. Любой `FAIL`, `BLOCKED` или `NOT RUN` означает, что Feature 007 ещё не принята.

- Final reviewers: `Codex independent reviewer /root/review_t050` (T050), `Codex independent reviewer /root/acceptance_reviewer` (T051–T053); `/root/review_t050` также выполнил итоговый peer audit всех evidence.
- Decision date (UTC): `2026-07-14T00:21:25Z`
- Decision evidence: `data/generated/research/acceptance/t050-20260713T235522Z-98d6972e/evidence.json` (`sha256:9f7995f04a3f903acc3153de81a9f6b6c423709b6e15c10e7d111d615e21458b`); `data/generated/research/acceptance/t051-t052-20260714T000154Z/evidence.json` (`sha256:00fb07d13655df9f5d8c4d866e79484d5ace308f5f080edc3bf9ef7dec1f5073`); `data/generated/research/acceptance/t053-20260713T234941Z/evidence.json` (`sha256:7a725ed0811f74c0c16cd51d2226b12d405fb4a3148b6e6470812f89798348b5`).
- Deviations accepted by owner: нет. Приёмка намеренно использовала isolated synthetic safe fixture вместо owner corpus, чтобы не раскрывать приватные excerpts; это запланированная acceptance boundary, а не ослабление контракта.
