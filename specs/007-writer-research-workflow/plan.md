# Implementation Plan: Writer/Research Workflow

**Branch**: `codex/007-writer-research-workflow` | **Date**: 2026-07-12 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/007-writer-research-workflow/spec.md`

## Summary

V5 добавляет локальный provenance-first workflow поверх существующего ArangoDB retrieval: точные chunk-level evidence сначала собираются в проверяемую immutable revision исследовательского досье, затем пользователь курирует её через include/exclude/pin и формирует versioned handoff для доверенного внешнего writing-agent. Агент возвращает structured writing-output package вида `draft` или `summary`; `knowledge-base` не вызывает модель, а проверяет package integrity, dossier/citation references и structural coverage, после чего атомарно сохраняет generated writing artifact отдельно от canonical данных.

Технический подход сохраняет текущие границы проекта: новые runtime-зависимости не нужны; ArangoDB используется read-only; generated artifacts живут file-first в `data/generated/research/`; MCP остаётся read-only; published-only действует только в V5 и передаётся внутрь candidate retrieval/graph expansion до ранжирования. Existing CLI/MCP defaults не меняются.

## Technical Context

**Language/Version**: Python `>=3.12`; JSON Schema 2020-12 как документируемый wire contract; Markdown как человекочитаемая проекция.

**Primary Dependencies**: Python stdlib и существующие модули проекта (`Settings`, `ArangoClient`, `KnowledgeRepository`, embedding/retrieval helpers). Новых обязательных runtime dependencies нет; optional `mcp` extra не меняется.

**Storage**: ArangoDB только для read queries; immutable directory packages под `data/generated/research/` для dossier revisions, handoffs и imported drafts/summaries. Explicit custom output root разрешён с unsafe-location warning и отдельным location acknowledgement; external-disclosure acknowledgement остаётся независимым handoff gate. Новые Arango collections/edges/indexes не создаются.

**Testing**: pytest unit tests для canonical JSON/hash/ID, selection, curation, schema parsing, renderer и package validation; live-ArangoDB integration для scoped retrieval, provenance, draft isolation и no-mutation; ruff, mypy, coverage, ADR check и contract fixture checks.

**Target Platform**: локальный POSIX CLI (macOS/Linux), offline для build/validate/curate/import; взаимодействие с writing-agent — ручной файловый round-trip.

**Project Type**: один Python package с CLI и библиотечными service functions; внешний writing-agent не является частью runtime проекта.

**Performance Goals**: первое dossier на текущем корпусе (≈2 972 documents / 24 877 chunks) создаётся не более чем за 30 секунд; повторная local validation bounded package выполняется не более чем за 5 секунд без учёта доступности ArangoDB; порядок evidence детерминирован при неизменных входах.

**Constraints**: published-only V5 default; любой handoff требует explicit egress acknowledgement, а drafts — второго подтверждения; no raw payload/structured credentials/local archive paths; unstructured excerpts могут содержать sensitive text и требуют owner review; full chunk — минимальная evidence unit; source/graph leads без chunk anchor не являются evidence; atomic publish; immutable revisions; incoming writing-output package недоверен; owner-only default permissions и symlink refusal; MCP и legacy retrieval semantics неизменны; structural validation не называется factual verification.

**Scale/Scope**: query `1..1000` Unicode code points; default 12 documents × 2 fragments, caps 50 documents / 5 fragments per document / 100 selected evidence; candidate pool cap 150; handoff/writing-output JSON cap 2 MiB; generated content cap 200 sections и 1 MiB Markdown. Значения являются safety bounds, а не обещанием исчерпывающего corpus review.

## Constitution Check

`.specify/memory/constitution.md` остаётся незаполненным upstream-шаблоном и не задаёт ратифицированных gates. До отдельного constitution workflow применяются обязательные проектные инварианты из `AGENTS.md` и accepted ADR 0004–0009.

| Gate | Design evidence | Status |
|------|-----------------|--------|
| Provenance обязателен | Каждый evidence содержит source/document/chunk identity, normalized offsets, URL и allowlisted raw/import linkage | PASS |
| Raw / processed / generated разделены | ArangoDB читается без mutations; все dossier/handoff/writing artifacts пишутся только в generated zone или explicit warned location | PASS |
| Generated не становится source of truth | Draft/summary явно маркируется, ссылается на immutable dossier revision и проходит citation coverage validation | PASS |
| Privacy и персональные данные | Published-only до ranking; каждый handoff требует egress acknowledgement, drafts — второго opt-in; structured credential/cookie/raw/path fields исключены, excerpts помечены potentially sensitive | PASS |
| Воспроизводимость | Canonical content digest, schema versions, request/settings/freshness metadata и deterministic tie-breaks | PASS |
| MCP read-only boundary | Ни одного MCP write-tool; обмен с writing-agent только через files и локальный CLI | PASS |
| Соразмерность зависимостей | Stdlib-only runtime; новые модули малы и независимо тестируются | PASS |
| Архитектурные решения имеют принятый ADR | Принятый [ADR 0010](../../docs/adr/0010-adopt-provenance-gated-writer-research-file-workflow.md) фиксирует V5 trust/data/citation boundary | PASS |

Post-design re-check: data model, schemas and CLI contract ниже не вводят canonical writes, network/model calls или глобальное изменение visibility. Все gates проходят; ADR 0010 принят до начала implementation.

## Project Structure

### Documentation (this feature)

```text
specs/007-writer-research-workflow/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── acceptance.md
├── checklists/
│   └── requirements.md
└── contracts/
    ├── cli.md
    ├── citation.schema.json
    ├── dossier-manifest.schema.json
    ├── handoff-package.schema.json
    ├── writing-output-package.schema.json
    ├── imported-writing-manifest.schema.json
    └── validation-result.schema.json
```

### Source Code (repository root)

```text
src/knowledge_base/
├── research_retrieval.py   # visibility-scoped chunk candidates + exact hydration
├── research_workflow.py    # selection, dossier build, curation and orchestration
├── research_artifacts.py   # canonical JSON, IDs/hashes, renderers, atomic directory I/O
├── writing_handoff.py      # handoff creation and untrusted writing-output validation/import
└── cli/main.py             # thin `kb research ...` command adapters

tests/
├── unit/
│   ├── test_research_retrieval.py
│   ├── test_research_workflow.py
│   ├── test_research_artifacts.py
│   └── test_writing_handoff.py
├── integration/
│   └── test_research_workflow_pipeline.py
└── fixtures/research/
    ├── valid-writing-output-draft.json
    ├── valid-writing-output-summary.json
    └── invalid-writing-output.json

docs/
├── adr/0010-adopt-provenance-gated-writer-research-file-workflow.md
├── architecture.md
└── roadmap.md
```

**Structure Decision**: сохранить single-package архитектуру. Arango-specific read logic изолируется в `research_retrieval.py`; pure domain/selection — в `research_workflow.py`; filesystem/wire concerns — в `research_artifacts.py`; граница внешнего агента — в `writing_handoff.py`. CLI не содержит business logic. MCP-код не меняется.

## Design Sequence

1. **Scoped evidence retrieval**: ввести internal `ResearchVisibility`, visibility-aware text/vector/related queries и exact chunk hydration. Legacy calls получают прежний default `None`; V5 всегда передаёт explicit status scope.
2. **Deterministic dossier build**: объединить lexical/semantic chunk scores, отделить leads от evidence, применить per-document caps и round-robin diversity с явными tie-breaks; построить bounded candidate pool и selected evidence.
3. **Immutable artifact layer**: canonical JSON, non-circular digest/ID projections, strict parsers, Markdown renderer, generated-zone classifier, owner-only permissions, symlink refusal, temporary-directory + atomic-rename publisher и dossier validation.
4. **Curation revisions**: include/exclude/pin только по разрешимому candidate pool, parent lineage, immutable new revision, stable evidence IDs и новый content digest.
5. **Writing-agent round-trip**: versioned handoff JSON, mandatory egress acknowledgement plus explicit draft-evidence confirmation, strict incoming writing-output validation for both `draft` and `summary`, section coverage и atomic generated artifact.
6. **CLI, docs and gates**: команды из [contracts/cli.md](contracts/cli.md), integration/no-mutation tests, README/architecture/roadmap sync и ADR 0010.

## Validation Strategy

- Pure tests фиксируют canonical serialization, collision handling, stable citation IDs, content digests, deterministic ordering, pin/exclude/include semantics и immutable parent behavior.
- Contract fixtures проверяются и JSON Schemas, и тем же strict parser, который работает в runtime; `additionalProperties` запрещены на внешних envelopes.
- Integration corpus содержит published + draft документы, related edges и tainted community. Тест доказывает, что draft не влияет на V5 candidates/context без opt-in и появляется только с opt-in.
- Каждый citation сверяется с текущими document/chunk records, `normalized_whitespace_v1` offsets, excerpt hash, chunk ownership и allowlisted provenance edges.
- Counts/hashes всех Arango collections до и после build/validate/curate/handoff/import совпадают.
- Failure injection проверяет cleanup temporary directories, package size/schema mismatch, wrong dossier/handoff digest, unknown citations и changed/missing chunks.
- Path-safety tests проверяют default `0700` directories / `0600` files, symlink refusal, warning+acknowledgement для custom output outside `data/generated/` и отсутствие чтения paths из incoming package.
- Real-corpus acceptance измеряет build time, artifact sizes, deterministic content digest и отсутствие raw/structured-private fields; exact excerpts рассматриваются как potentially sensitive и проходят owner review перед handoff.
- Independent acceptance выполняет владелец или назначенный reviewer по [acceptance.md](acceptance.md): отдельно принимаются dossier/citation/curation contour, `draft` round-trip, `summary` round-trip и privacy/path-safety boundary. Feature 007 нельзя закрыть без записанного результата всех четырёх секций.

## Complexity Tracking

Нарушений gate нет. Четыре модуля отражают четыре независимые границы ответственности (DB reads, domain workflow, immutable files, untrusted agent exchange); объединение их в один модуль усложнило бы privacy и contract testing.
