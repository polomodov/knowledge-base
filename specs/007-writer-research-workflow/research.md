# Phase 0 Research: Writer/Research Workflow

Все решения ниже основаны на уточнённой [feature specification](spec.md), текущем коде и ADR 0004–0009. Неразрешённых `NEEDS CLARIFICATION` после исследования нет.

## 1. Evidence unit и citation identity

**Decision**: минимальная evidence unit — полный persisted chunk. Title-only, graph-only и community results являются leads; они становятся evidence только после выбора конкретного visible chunk. `citation_id` — content-derived `cit-<sha256-prefix>` по canonical projection: projection version, `source_key`, `canonical_id`, `document_key`, `chunk_key`, normalized offsets и `excerpt_sha256`.

**Rationale**: текущий search snippet — первые 240 символов hit и не является устойчивой цитатой; `chunk_key` уже зависит от ordinal и hash текста. Content-derived ID остаётся стабильным при неизменном normalized fragment и меняется при его изменении. Raw/import run keys остаются provenance metadata, но не входят в identity, чтобы повторный ingest неизменного текста не переименовывал citation.

**Alternatives considered**:

- document-level citation — слишком грубая и не доказывает точный excerpt;
- retrieval snippet — неполный и позиционно неоднозначный;
- произвольный sub-chunk quote — требует нового selection/offset contract;
- import-run-derived ID — нестабилен между эквивалентными ingest runs.

## 2. Visibility-scoped retrieval без breaking change

**Decision**: новые V5 helpers в `research_retrieval.py` требуют explicit `ResearchVisibility` (`published_only` / `published_and_drafts`) и возвращают exact multi-chunk evidence candidates. Existing helpers в `retrieval.py` и вызывающие их legacy CLI/MCP surfaces не меняются и сохраняют текущую семантику. V5 применяет scope в собственных AQL queries до ranking/dedup, при vector hydration, related expansion и derived grouping.

**Rationale**: post-filter top-K не устраняет влияние draft на ranking и graph expansion. Глобальная смена default сломала бы зафиксированное поведение существующих read surfaces. V5 нужен собственный disclosure boundary, потому что handoff покидает доверенную DB/CLI зону.

**Alternatives considered**:

- глобальный published-only — безопаснее, но является отдельным breaking contract;
- post-filter готовых results — скрывает строку, но не её влияние;
- отдельные persisted indexes по status — расширяют storage/index lifecycle;
- использовать stored community summaries — они могут быть tainted скрытыми drafts.

## 3. Chunk candidate retrieval и детерминированный selection

**Decision**: candidate discovery выполняется lexical + semantic сигналами на уровне chunks с explicit status/source/date scope. Semantic discovery использует bounded overfetch, затем exact cosine re-score для hydrated chunks; final fusion и tie-breaks выполняются детерминированно. Evidence выбирается round-based: сначала лучший grounded chunk каждого документа, затем следующие chunks до per-document cap. Exact duplicates удаляются по citation identity; pin повышает presentation priority, но не переписывает retrieval score.

**Rationale**: существующий hybrid дедуплицирует на уровне документа и graph-only rows могут не иметь chunk. Отдельный V5 read model нужен для точных offsets и нескольких fragments на документ. Bounded overfetch сохраняет индексную скорость, exact re-score и stable sort дают повторяемый final order. Round-based selection не позволяет одному длинному документу вытеснить diversity.

**Alternatives considered**:

- напрямую считать hybrid results evidence — часть rows не имеет chunk anchor;
- полный exact scan всех embeddings при каждом запросе — детерминирован, но передаёт весь vector corpus и рискует нарушить 30-second goal;
- ANN order как final order — быстрее, но не является нормативным deterministic tie-break;
- только BM25 — теряет semantic recall.

## 4. Derived graph context

**Decision**: persisted communities/summaries не входят в published-only evidence или handoff, если их clean visibility не доказана. V5 группирует выбранные visible documents по их visible topics и может использовать visibility-filtered related edges только как leads. Любой утверждающий context обязан ссылаться на grounded chunks.

**Rationale**: communities построены над более широким корпусом и могут раскрыть draft-only topics через aggregate summary. V4 уже применяет tainted-rollup suppression. Для первого V5 среза bounded grouping из selected visible evidence проще и честнее.

**Alternatives considered**:

- удалить draft citations из stored summary — скрытое влияние остаётся;
- полностью перестраивать communities per request — дорого и расширяет scope;
- отказаться от graph context — безопасно, но теряет полезные related leads.

## 5. Immutable directory revisions

**Decision**: dossier хранится как immutable directory revision: `manifest.json`, `dossier.md`, `validation.json`. Package полностью пишется в sibling temporary directory, fsync-ится и атомарно переименовывается. Каждый run получает новый `revision_id`; deterministic `content_digest` хранится отдельно и совпадает у эквивалентных запусков.

**Rationale**: независимая запись Markdown и JSON может оставить рассогласованный artifact. Уникальный revision ID удовлетворяет immutable history, а content digest — reproducibility. Files остаются generated outputs и не требуют новых DB collections.

**Alternatives considered**:

- mutable `latest.*` — ломает lineage;
- один Markdown — не поддаётся strict validation;
- Arango collection для research sessions — смешивает generated и canonical zones;
- ZIP — добавляет archive/path risks без пользы локальному round-trip.

## 6. Curation model

**Decision**: initial revision сохраняет bounded candidate pool и selected evidence. Include разрешён только для candidate с тем же visibility scope; exclude и pin — только для current selected set. Каждая non-empty операция создаёт child revision с `parent_revision_id`, ordered operations и новым digest. No-op и ссылки на неизвестные evidence отклоняются.

**Rationale**: bounded candidate pool позволяет курировать без скрытого повторного retrieval и делает lineage воспроизводимым. Immutable child revision сохраняет то, какой набор был передан writing-agent.

**Alternatives considered**:

- редактировать manifest вручную — обходит validation;
- rebuild после каждого include — меняет candidate universe;
- хранить только финальный set — теряет причину изменения;
- writing-agent сам выбирает evidence — выбор не попадает в provenance chain.

## 7. Writing-agent file round-trip

**Decision**: handoff — один versioned JSON envelope с выбранными evidence, citation allowlist, query/scope, instructions и digest. Writing-agent возвращает один versioned writing-output JSON envelope с `output_kind=draft|summary`, `content_markdown`, section coverage, dossier/handoff IDs и self-reported agent metadata. `knowledge-base` не вызывает сеть/модель, не хранит provider credentials и не добавляет MCP writes.

Handoff identity считается без цикла: сначала canonical identity projection исключает `created_at`, `handoff_id`, `identity_sha256` и `package_digest`; её digest становится `identity_sha256`, а prefix — `handoff_id`. После вставки этих полей `package_digest` считается над полным envelope без `created_at` и самого `package_digest`.

**Rationale**: один structured envelope легче ограничить по размеру, валидировать и переносить, чем directory/ZIP protocol. External agent получает ровно раскрытый handoff, а его output остаётся недоверенным input. Existing MCP ADR сохраняется.

**Alternatives considered**:

- прямой provider/API client — новая privacy/cost/credential граница;
- MCP write tool — нарушает read-only contract;
- agent пишет в generated directory — обходит schema/atomicity;
- plain text paste — теряет dossier identity и section coverage.

## 8. Writing-output validation semantics

**Decision**: automatic validation доказывает только `schema_valid`, `package_integrity`, `dossier_current`, `citations_resolved` и `coverage_complete`. Она не утверждает factual entailment. Каждый section содержит известные citation IDs либо `unsupported_by_corpus=true` с причиной. `human_reviewed` — отдельный false-by-default флаг, который автоматическая команда не выставляет.

**Rationale**: присутствие citation не доказывает поддержку тезиса. Честное разделение structural и semantic/human validation предотвращает ложную гарантию.

**Alternatives considered**:

- назвать coverage factual validation — вводит пользователя в заблуждение;
- LLM entailment judge — возвращает provider/privacy/evaluation scope, от которого пользователь отказался;
- запретить unsupported sections — мешает авторским связкам и гипотезам.

## 9. Package security и limits

**Decision**: incoming envelopes используют strict version dispatch, `additionalProperties: false`, bounded nesting/counts/bytes и никогда не трактуют package paths/URLs как команды на чтение. Handoff excerpts маркируются как untrusted quoted data; raw payload, archive refs, local paths и структурные config/env credentials/cookies исключаются allowlist projection. Exact excerpt может сам содержать sensitive text, поэтому каждый handoff требует explicit `--acknowledge-external-disclosure`; `include_drafts=true` дополнительно требует `--allow-draft-evidence`.

Default output root проверяется без follow symlinks; новые package directories создаются mode `0700`, files — `0600`. Explicit path вне `data/generated/` получает warning и отдельное acknowledgement, но symlink final/components всё равно отклоняются. Невозможность гарантировать POSIX permissions является hard error для default root.

**Rationale**: writing-agent output может содержать ошибочные или adversarial fields даже в local workflow. Generated zone является plaintext storage под OS trust, а не encryption boundary. `published` описывает lifecycle документа, а не согласие на external disclosure, поэтому подтверждение требуется для любого handoff. Allowlist может исключить structured secret fields, но не может честно обещать secret-free unstructured text.

**Alternatives considered**:

- доверять package целиком — не защищает от prompt injection/ошибок;
- cryptographic signing — требует key management, избыточный для single-user local scope;
- silently follow paths/URLs или symlink output — создаёт path traversal/SSRF/filesystem boundary;
- шифровать artifacts в V5 — требует отдельного key lifecycle.

## 10. Validation, degradation and exit behavior

**Decision**: build создаёт finalized revision только при наличии хотя бы одного valid evidence. Optional graph/context degradation даёт `status=degraded`, warning и exit 0, если evidence/citations valid. `no_evidence`, invalid schema/package, citation mismatch и atomic publish failure дают exit 1 и не создают valid artifact. Validation reports могут быть выведены в stdout или отдельный failed-attempt path, но не внутри immutable valid revision. `draft` и `summary` проходят один writing-output contract и отдельные independent acceptance cases.

**Rationale**: warning-only degraded context не должен блокировать useful dossier; отсутствие grounded evidence или integrity failure — hard stop. Это совместимо с текущим JSON-emitting CLI style.

**Alternatives considered**:

- всегда exit 0 — automation не различает rejection;
- блокировать любое degradation — хрупко при optional graph layer;
- сохранять invalid package как normal writing artifact — путает outcome;
- автоматически rebuild indexes — write/mutation выходит за V5.

## 11. ADR boundary

**Decision**: ADR 0010 о provenance-gated writer/research file workflow принят до реализации. Он фиксирует citation identity, immutable generated revisions, V5-only visibility, tainted context policy, external-agent round-trip и structural-vs-factual validation. ADR 0004–0008 не supersede.

**Rationale**: решения меняют долговременные privacy, provenance и generated-output contracts, которые будущему участнику нужно понимать по причинам, а не только по diff.

**Alternatives considered**:

- оставить решения только в spec/plan — недостаточная архитектурная трассируемость;
- переписать accepted ADR — нарушает ADR history;
- отдельный ADR на каждый package — избыточно для одного связного V5 boundary.
