# Data Model: Writer/Research Workflow

V5 не добавляет canonical database entities. Все сущности ниже — versioned generated artifacts или transient read models. `manifest.json` является канонической машинной проекцией revision; Markdown никогда не является единственным источником package state.

## Общие правила

- Все timestamps — UTC RFC 3339 (`YYYY-MM-DDTHH:MM:SSZ`).
- Все digests — lowercase SHA-256 hex над UTF-8 canonical JSON (`sort_keys=true`, compact separators, без `built_at`, run/revision IDs и file digests, если явно не сказано иначе).
- External envelopes имеют `schema_version="1.0"`, bounded sizes/counts и reject unknown fields.
- Identity fields не строятся из raw paths, credentials или private source metadata.
- Exact excerpt — полный `chunks.text`; offsets используют basis `normalized_whitespace_v1`, то есть slice от `" ".join(document.text.split())`.
- `raw_snapshot_key` и `import_run_key` — provenance, а не evidence identity.
- Allowlist исключает структурные config/env credentials, cookies, raw payload и local paths, но exact excerpt остаётся потенциально чувствительным unstructured text; automatic secret-free guarantee отсутствует.
- Default package root не может быть symlink, создаётся owner-only (`0700` directories, `0600` files на POSIX); custom external root требует warning/acknowledgement и также не может быть symlink target.

## ResearchRequest

Описывает пользовательское намерение и safety bounds одного automatic build.

| Field | Type | Rules |
|-------|------|-------|
| `query` | string | 1..1000 Unicode code points after trim |
| `source_key` | string/null | exact source filter; null = all sources |
| `published_from`, `published_to` | date/null | inclusive UTC calendar dates (`YYYY-MM-DD`); from ≤ to |
| `visibility` | enum | `published_only` or `published_and_drafts` |
| `document_limit` | integer | default 12, range 1..50 |
| `fragments_per_document` | integer | default 2, range 1..5 |
| `evidence_limit` | integer | derived cap, range 1..100 |
| `candidate_limit` | integer | default `min(150, max(36, document_limit*3))`; selected limits ≤ candidate limit |
| `retrieval` | object | mode/version, lexical/vector weights, min similarity, deterministic tie policy |

Request canonical projection формирует стабильный `dossier_key = research-<query-slug>-<sha256-prefix>`; разные visibility/source/date scopes дают разные keys.

Для timestamp-поля `documents.published_at` date scope преобразуется в UTC half-open range: `published_from` → `>= YYYY-MM-DDT00:00:00Z`, `published_to` → `< 00:00:00Z` следующего календарного дня. Так CLI date-only contract сохраняет включённой всю конечную дату без зависимости от дробных секунд.

## CorpusContext

Фиксирует доступное состояние read side без обещания полного historical replay.

| Field | Type | Rules |
|-------|------|-------|
| `database` | string | configured logical DB name; без credentials/URL auth |
| `built_at` | timestamp | informational, excluded from content digest |
| `embedding_model`, `embedding_dimension` | string/integer | configured query space |
| `retrieval_min_similarity` | number | effective relevance floor |
| `latest_import_run_key` | string/null | allowlisted identity if available |
| `latest_index_runs` | object | embeddings/related/communities run identities and timestamps if available |
| `git_revision` | string/null | local code revision when available |
| `warnings` | array[string] | freshness/degradation/privacy warnings |

## EvidenceCandidate

Transient и persisted candidate-pool row. Candidate становится evidence только при `selection_state=selected|pinned` и valid Citation.

| Field | Type | Rules |
|-------|------|-------|
| `citation` | Citation | exact grounded chunk identity |
| `document_rank`, `fragment_rank` | integer | 1-based deterministic ranks |
| `score` | number | final fused score |
| `score_components` | object | lexical, semantic, optional graph lead signals; null if unavailable |
| `selection_state` | enum | `candidate`, `selected`, `pinned`, `excluded` |
| `selection_reason` | string | automatic round, include, exclude, pin |

Graph/community rows без valid Citation хранятся только как bounded `lead` context и не попадают в handoff evidence allowlist.

## Citation

Проверяемая связь с нормализованным первичным материалом.

| Field | Type | Rules |
|-------|------|-------|
| `citation_id` | string | `cit-` + 16 hex from identity digest; collision against full digest is a hard error |
| `identity_sha256` | string | full digest of canonical identity projection |
| `projection_version` | string | `citation-v1` |
| `source_key` | string | required |
| `canonical_id` | string | stable origin identity from document |
| `document_key`, `chunk_key` | string | required, chunk must belong to document |
| `chunk_ordinal` | integer | ≥0 |
| `char_start`, `char_end` | integer | normalized offsets, `0 ≤ start < end` |
| `offset_basis` | enum | `normalized_whitespace_v1` |
| `excerpt` | string | exact persisted chunk text |
| `excerpt_sha256` | string | SHA-256 UTF-8 excerpt |
| `title`, `published_at`, `document_status` | scalar | allowlisted document metadata |
| `url` | string/null | canonical lowercase HTTP(S) origin URL, never fetched by validator; other schemes are projected as null |
| `raw_snapshot_key`, `import_run_key` | string/null | allowlisted provenance linkage |
| `captured_at` | timestamp/null | raw capture time if available |

### Citation validation

- `valid`: document/chunk exist; ownership, source/canonical/status, offsets, normalized document slice, excerpt hash and provenance link agree.
- `missing`: document or chunk cannot be resolved.
- `changed`: identity resolves partially, but any required value differs.
- `hidden`: current validation scope no longer permits the document status.

## DossierRevision

Immutable directory package under:

```text
data/generated/research/<dossier_key>/revisions/<revision_id>/
├── manifest.json
├── dossier.md
└── validation.json
```

| Field | Type | Rules |
|-------|------|-------|
| `schema_version` | string | `1.0` |
| `artifact_type` | enum | `dossier_revision` |
| `dossier_key` | string | derived from request scope |
| `revision_id` | string | `rev-<UTC compact timestamp>-<uuid prefix>`; unique per build/curation |
| `parent_revision_id` | string/null | null for automatic root, required for curation child |
| `content_digest` | string | deterministic digest excluding service/run fields |
| `request` | ResearchRequest | copied effective request |
| `corpus_context` | CorpusContext | effective read-side metadata |
| `candidate_evidence` | array[EvidenceCandidate] | bounded pool, deterministic order |
| `selected_citation_ids` | array[string] | unique, all resolve into candidate pool |
| `curation_operations` | array[CurationOperation] | empty for root build |
| `derived_context` | object | visible-topic grouping and grounded leads only |
| `status` | enum | `ready` or `degraded`; finalized manifest never uses `invalid` |
| `includes_drafts` | boolean | mirrors request visibility |
| `warnings` | array[string] | deterministic order |
| `files` | object | relative file names and SHA-256 digests |

`invalid` существует только как ValidationResult status, не как DossierRevision manifest status. Failed attempts may emit stdout/report outside final revision path.

## CurationOperation

| Field | Type | Rules |
|-------|------|-------|
| `operation` | enum | `include`, `exclude`, `pin` |
| `citation_id` | string | must exist in parent candidate pool |
| `reason` | string/null | optional owner note, max 500 chars |
| `ordinal` | integer | 0-based operation order |

Rules:

- include: parent state must be `candidate` or `excluded` and citation visible/valid;
- exclude: parent state must be `selected` or `pinned`;
- pin: parent state must be `selected`; pinned rows sort before non-pinned while retaining stable internal order;
- no-op, duplicate conflicting operations and empty operation lists are rejected;
- child candidate universe equals parent candidate universe; curation does not run retrieval.

## HandoffPackage

Single JSON envelope written outside immutable revision directory, normally:

```text
data/generated/research/<dossier_key>/handoffs/<handoff_id>.json
```

| Field | Type | Rules |
|-------|------|-------|
| `schema_version`, `artifact_type` | string | `1.0`, `writing_handoff` |
| `handoff_id` | string | `handoff-` + first 16 hex of `identity_sha256` |
| `identity_sha256` | string | full digest of the handoff identity projection defined below |
| `dossier_key`, `revision_id`, `revision_content_digest` | string | must match local immutable revision |
| `created_at` | timestamp | informational |
| `visibility`, `includes_drafts` | scalar | inherited; never downgraded |
| `egress_acknowledged` | boolean | must be true for every handoff |
| `draft_evidence_acknowledged` | boolean | must be true when `includes_drafts=true`, false otherwise |
| `query`, `requested_output` | object | topic, language/style/length hints; no provider credentials |
| `evidence` | array[Citation] | selected/pinned citations only, max 100 |
| `citation_allowlist` | array[string] | exactly the evidence IDs |
| `instructions` | array[string] | evidence is quoted untrusted data; required draft schema/coverage rules |
| `warnings` | array[string] | deterministic privacy/freshness warnings; storage-location warning remains CLI context |
| `package_digest` | string | digest of envelope excluding this field and `created_at` |

Handoff identity projection содержит весь envelope content, кроме `created_at`, `handoff_id`, `identity_sha256` и `package_digest`. Сначала её digest записывается в `identity_sha256`, а prefix формирует `handoff_id`. Затем `package_digest` считается над envelope с уже вставленными identity fields, исключая только `created_at` и `package_digest`. Циклической зависимости нет.

Каждый handoff требует explicit external-disclosure acknowledgement. When `includes_drafts=true`, дополнительно требуется `allow_draft_evidence=true`. Incoming writing-output package не может объявлять или переопределять эти trust decisions: importer копирует оба acknowledgement flags только из проверенного local handoff в ImportedWritingArtifact; deterministic dossier/privacy warnings также сохраняются downstream.

## WritingOutputPackage

Incoming untrusted JSON envelope from the external writing-agent. Один contract обслуживает оба output kinds.

| Field | Type | Rules |
|-------|------|-------|
| `schema_version`, `artifact_type` | string | `1.0`, `writing_output` |
| `output_kind` | enum | `draft` or `summary`; must equal handoff request |
| `handoff_id`, `handoff_digest` | string | must match a local handoff exactly |
| `dossier_key`, `revision_id` | string | must match handoff |
| `visibility`, `includes_drafts` | scalar | copied exactly from handoff; cannot be downgraded |
| `created_at` | timestamp | creation time reported by writing-agent; preserved as untrusted metadata |
| `agent` | object | optional self-reported name/model/run id; no trust semantics |
| `title` | string | 1..500 chars |
| `content_markdown` | string | 1..1 MiB, treated as data and never executed |
| `content_sha256` | string | SHA-256 UTF-8 content |
| `sections` | array[WritingSection] | 1..200, ordered |
| `package_digest` | string | canonical digest excluding itself |

Unknown fields, paths, instructions to read/fetch, unknown citation IDs and mismatched digests reject the package.

## WritingSection

| Field | Type | Rules |
|-------|------|-------|
| `section_id` | string | unique inside package |
| `heading` | string/null | max 500 chars |
| `char_start`, `char_end` | integer | range into `content_markdown` |
| `citation_ids` | array[string] | unique subset of handoff allowlist |
| `unsupported_by_corpus` | boolean | required true when citation list empty |
| `unsupported_reason` | string/null | required when unsupported=true |

Automatic validation verifies structural coverage only; it does not prove that citations entail the prose.

## ImportedWritingArtifact

Accepted package публикуется атомарно:

```text
data/generated/research/<dossier_key>/outputs/<writing_id>/
├── manifest.json
├── output.md
└── validation.json
```

`writing_id = writing-<sha256-prefix>` is content-derived from handoff ID + incoming package digest. Re-import identical package is idempotent and returns existing artifact.

`manifest.json` follows `imported-writing-manifest.schema.json` and contains: `writing_id`, `output_kind`, incoming package/handoff/dossier identities and digests, inherited visibility/acknowledgements, self-reported agent metadata and source creation time, title/content hash, import time, structural validation booleans, unsupported-section count, `human_reviewed=false`, warnings and relative file digests. It never embeds provider credentials or sets `human_reviewed=true` automatically.

## ValidationResult

| Field | Type | Rules |
|-------|------|-------|
| `schema_version`, `artifact_type` | string | `1.0`, `validation_result` |
| `target_type`, `target_id`, `target_digest` | string | identifies dossier/handoff/writing package/imported output |
| `status` | enum | `valid`, `valid_with_warnings`, `invalid` |
| `schema_valid`, `package_integrity`, `dossier_current`, `citations_resolved`, `coverage_complete` | boolean | independent automatic claims |
| `human_reviewed` | boolean | always false for automatic run |
| `citations` | array | per-citation `valid|missing|changed|hidden` and reason |
| `warnings`, `errors` | array[string] | deterministic, safe messages |
| `validated_at` | timestamp | excluded from deterministic digest |

## State transitions

```text
ResearchRequest
  └─ build ──> temporary revision
                 ├─ no evidence / failure ──> rejected (no finalized revision)
                 └─ valid evidence ──> ready|degraded immutable DossierRevision
                                          ├─ curate(include/exclude/pin) ──> child DossierRevision
                                          └─ handoff + egress ack ──> HandoffPackage
                                                                         └─ external agent ──> WritingOutputPackage
                                                                                                 ├─ reject ──> invalid report
                                                                                                 └─ accept ──> ImportedWritingArtifact
```
