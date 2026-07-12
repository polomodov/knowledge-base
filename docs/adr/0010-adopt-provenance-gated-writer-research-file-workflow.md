# 0010. Принять provenance-gated файловый writer/research workflow / Adopt provenance-gated writer/research file workflow

```json adr-meta
{
  "id": "0010",
  "titleRu": "Принять provenance-gated файловый writer/research workflow",
  "titleEn": "Adopt provenance-gated writer/research file workflow",
  "status": "proposed",
  "date": "2026-07-12",
  "deciders": [
    "knowledge-base maintainer"
  ],
  "tags": [
    "writer-workflow",
    "provenance",
    "privacy",
    "generated-data",
    "mcp"
  ],
  "supersedes": [],
  "supersededBy": []
}
```

## RU

### Контекст и проблема

После завершения retrieval, GraphRAG, локального read-only MCP и offline visualization следующий этап roadmap — writer/research workflow: из темы собрать релевантные фрагменты, сохранить проверяемые citations, подготовить исследовательскую подборку и получить generated draft или summary.

Эта фича пересекает несколько уже принятых границ. Generated output не должен становиться canonical source; imported drafts сейчас видимы обычным CLI/MCP reads; persisted community summaries могли быть построены на более широком корпусе; MCP намеренно остаётся read-only; прямое подключение LLM/provider потребовало бы credentials, privacy, cost и evaluation contract. При этом простой Markdown без machine-readable provenance нельзя воспроизвести или строго проверить, а разрешение writing-agent самостоятельно писать в generated-зону обходит validation и atomicity.

Нужно определить долговременный контракт между нормализованными источниками, evidence dossier и внешним writing-agent так, чтобы точные цитаты оставались проверяемыми, пользователь мог курировать immutable revisions, а модель и её credentials не входили в trust boundary `knowledge-base`.

### Y-statement

В контексте локального writer/research workflow поверх личной базы знаний, столкнувшись с необходимостью передавать точные фрагменты внешнему writing-agent без смешивания generated и canonical данных и без нарушения read-only MCP boundary, мы решили выбрать provenance-gated file-first workflow с chunk-level citations, immutable dossier revisions и versioned handoff/writing-output envelopes, чтобы обеспечить проверяемость, воспроизводимость и явную privacy-границу, принимая ручной файловый round-trip и отсутствие автоматической factual verification.

### Драйверы решения

- Каждый использованный фрагмент должен разрешаться до нормализованного document/chunk и allowlisted source/raw/import provenance.
- Generated dossiers и writing outputs (`output_kind=draft|summary`) должны оставаться вне canonical Arango collections.
- Draft documents не должны влиять на V5 result без явного opt-in, включая ranking, graph expansion и derived context.
- Существующие CLI/MCP visibility defaults не должны меняться задним числом.
- MCP должен сохранить local stdio read-only contract из ADR 0004.
- Writing-agent output нельзя считать доверенным только потому, что агент локальный.
- Повторный build должен давать сравнимый content digest, а curation — сохранять immutable lineage.
- Structural citation coverage нельзя выдавать за фактическую поддержку утверждений.
- Core runtime должен остаться без новой обязательной LLM/agent зависимости.

### Рассмотренные варианты

1. **File-first dossier + внешний writing-agent + строгий import (выбрано).** `knowledge-base` строит и проверяет evidence, создаёт versioned handoff, принимает недоверенный structured writing-output package типа `draft` или `summary` и атомарно сохраняет generated artifact.
2. **Встроенный LLM/provider.** CLI сам вызывает модель и сохраняет draft или summary.
3. **MCP write tools.** Writing-agent создаёт research/writing-output artifacts через расширенный MCP server.
4. **Generated сущности в ArangoDB.** Dossiers, revisions и writing outputs становятся новыми collections.
5. **Неструктурированный Markdown round-trip.** Агент получает/возвращает только текстовые файлы без нормативного manifest/schema.

### Итоговое решение

Выбран file-first provenance-gated workflow со следующими правилами:

1. **Evidence unit и identity.** Evidence — полный persisted chunk, а не 240-character retrieval snippet. Title-only, graph-only и community rows остаются leads, пока не выбрано конкретное visible chunk. Citation ID выводится из canonical projection с version, source/canonical/document/chunk identity, normalized offsets и excerpt SHA-256; raw/import run keys сохраняются как provenance metadata, но не делают identity нестабильной между эквивалентными ingest runs.
2. **V5-only visibility.** Research pipeline явно выбирает `published_only` или `published_and_drafts` и применяет scope до candidate ranking, semantic hydration, graph expansion и derived grouping. Legacy CLI/MCP calls сохраняют текущее поведение. Stored derived context, который может быть tainted скрытым draft, подавляется или пересчитывается на visible subset.
3. **Immutable artifacts.** Dossier revision — directory package с canonical manifest, Markdown projection и validation result под `data/generated/research/`. Package полностью пишется во временную sibling directory и атомарно переименовывается. Каждый run получает новый revision ID; deterministic content digest хранится отдельно.
4. **Curation lineage.** Include/exclude/pin разрешены только над bounded candidate pool проверенной parent revision. Каждая non-empty curation создаёт child revision с parent ID и ordered operation log; in-place mutation запрещена.
5. **External-agent round-trip.** `knowledge-base` создаёт versioned JSON handoff с selected evidence, citation allowlist и ожидаемым `output_kind`, где разрешены только `draft` и `summary`. Создание любого handoff требует явного подтверждения, записанного как `egress_acknowledged=true`: владелец подтверждает передачу точных excerpts выбранному внешнему writing-agent. Если evidence содержит импортированные draft-документы, требуется второе независимое подтверждение `draft_evidence_acknowledged=true`. Внешний writing-agent возвращает versioned JSON writing-output package с совпадающим `output_kind`. Проект не вызывает модель/сеть, не хранит provider credentials и не добавляет MCP writes.
6. **Untrusted import.** Incoming package ограничивается schema/version/bytes/counts, не может заставить validator читать path или URL, обязан совпасть с local handoff/dossier digest и может ссылаться только на allowlisted evidence IDs. Unknown fields/citations, mismatched identity или changed evidence отклоняют весь import.
7. **Честная validation.** Автоматическая проверка отдельно сообщает schema validity, package integrity, dossier freshness, citation resolution и structural coverage. Section writing output без citations обязан быть помечен `unsupported_by_corpus` с причиной. `human_reviewed` не выставляется автоматически, factual entailment не обещается.
8. **Sensitivity и owner review.** Dossier, handoff и writing output содержат точные excerpts и остаются plaintext generated artifacts под локальной OS trust boundary. Из package исключаются structured credentials, cookies, raw payload fields и локальные archive/file paths, но сами excerpts могут содержать чувствительный текст или secrets в свободной форме. Workflow не обещает secret-free excerpts: владелец обязан просмотреть selected evidence до acknowledgement и handoff.
9. **Filesystem boundary.** По умолчанию package directories создаются с POSIX mode `0700`, а файлы — `0600`. Workflow отказывается писать, если любой существующий компонент output path является symlink. Custom output вне `data/generated/` требует warning и отдельного CLI-подтверждения `--acknowledge-unsafe-output` до записи.
10. **Acceptance gate.** Feature 007 не считается завершённой до четырёх независимо записанных приёмок: dossier/citation/curation workflow, writing-output round-trip с `output_kind=draft`, writing-output round-trip с `output_kind=summary` и privacy/path-safety boundary. Успех одного output kind или автоматических тестов не заменяет остальные секции.

ADR не supersede ADR 0004–0008: MCP остаётся read-only, global legacy visibility не меняется, GraphRAG core остаётся extractive, а generated outputs по-прежнему не являются source of truth.

### Последствия

- Хорошо: любой evidence fragment и writing-output citation можно проверить до конкретного normalized chunk и provenance.
- Хорошо: immutable revisions и content digests дают воспроизводимое исследовательское lineage без новых DB сущностей.
- Хорошо: writing-agent получает минимально раскрытый bounded handoff вместо DB credentials или всего корпуса.
- Хорошо: V5 не создаёт breaking change для существующих CLI/MCP consumers.
- Хорошо: недоступность writing-agent не блокирует extractive dossier/curation workflow.
- Плохо: пользователь вручную передаёт handoff и возвращает writing-output package; автоматического one-command generation нет.
- Плохо: directory packages и несколько JSON schemas увеличивают объём contract/validation кода.
- Плохо: artifacts не зашифрованы; их защищают только локальные permissions и осторожность пользователя.
- Нейтрально: citation coverage доказывает структуру и разрешимость, но не семантическую истинность writing output.
- Нейтрально: полная historical replay невозможна без parser/schema/code/config fingerprint; content digest сравнивает доступную проекцию.
- Нейтрально: отдельный будущий ADR нужен для direct provider invocation, remote/shared workflow, MCP writes, encryption или automatic factual evaluation.

### План пересмотра

Пересмотреть решение, если:

- ручной file round-trip становится главным источником ошибок или неприемлемой задержки;
- появляется требование вызывать конкретную модель/API непосредственно из `knowledge-base`;
- writing-agent должен работать unattended, remote или от имени нескольких пользователей;
- требуется шифрование generated artifacts, signing packages или более сильная identity/auth модель;
- corpus scale нарушает 30-second dossier goal и требует persisted status-specific indexes;
- требуется полноценный editable research workspace, concurrent curation или merge conflicts;
- автоматическая factual/entailment evaluation получает отдельный измеримый quality contract;
- query-time visibility должно глобально измениться для CLI/MCP, а не только для V5.

### Ссылки

- [Feature 007 specification](../../specs/007-writer-research-workflow/spec.md)
- [Feature 007 implementation plan](../../specs/007-writer-research-workflow/plan.md)
- [ADR 0004: Local read-only MCP](0004-local-read-only-mcp-server-for-knowledge-base.md)
- [ADR 0005: Provenance and private archives](0005-define-source-provenance-and-private-archive-boundaries.md)
- [ADR 0006: Local security and privacy boundary](0006-define-the-local-security-and-privacy-trust-boundary.md)
- [ADR 0007: Rebuildable embeddings and extractive GraphRAG](0007-adopt-rebuildable-embeddings-and-extractive-graphrag.md)
- [ADR 0008: Offline visualization and graph export](0008-adopt-offline-visualization-and-graph-export.md)

## EN

### Context and Problem Statement

With retrieval, GraphRAG, local read-only MCP, and offline visualization complete, the next roadmap stage is a writer/research workflow: gather relevant fragments for a topic, preserve verifiable citations, curate a research collection, and obtain a generated draft or summary.

This feature crosses several accepted boundaries. Generated output must not become a canonical source; imported drafts are currently visible to ordinary CLI/MCP reads; persisted community summaries may have been built over a wider corpus; MCP intentionally remains read-only; and direct LLM/provider integration would require credentials, privacy, cost, and evaluation contracts. At the same time, plain Markdown without machine-readable provenance is not reproducible or strictly validatable, while allowing a writing agent to write directly into the generated zone bypasses validation and atomicity.

We need a durable contract between normalized source materials, an evidence dossier, and an external writing agent so exact citations remain verifiable, users can curate immutable revisions, and the model and its credentials remain outside the `knowledge-base` trust boundary.

### Y-statement

In the context of a local writer/research workflow over a personal knowledge base, facing the need to pass exact fragments to an external writing agent without mixing generated and canonical data or violating the read-only MCP boundary, we decided for a provenance-gated file-first workflow with chunk-level citations, immutable dossier revisions, and versioned handoff/writing-output envelopes to achieve verifiability, reproducibility, and an explicit privacy boundary, accepting a manual file round-trip and no automatic factual verification.

### Decision Drivers

- Every used fragment must resolve to a normalized document/chunk and allowlisted source/raw/import provenance.
- Generated dossiers and writing outputs (`output_kind=draft|summary`) must stay outside canonical Arango collections.
- Draft documents must not influence V5 output without explicit opt-in, including ranking, graph expansion, and derived context.
- Existing CLI/MCP visibility defaults must not change retroactively.
- MCP must preserve the local stdio read-only contract from ADR 0004.
- Writing-agent output cannot be trusted merely because the agent is local.
- Repeated builds must produce comparable content digests, while curation must preserve immutable lineage.
- Structural citation coverage must not be presented as factual support for claims.
- The core runtime must remain free of a new mandatory LLM/agent dependency.

### Considered Options

1. **File-first dossier + external writing agent + strict import (chosen).** `knowledge-base` builds and validates evidence, creates a versioned handoff, accepts an untrusted structured writing-output package of kind `draft` or `summary`, and atomically stores a generated artifact.
2. **Built-in LLM/provider.** The CLI calls a model and stores the draft or summary itself.
3. **MCP write tools.** The writing agent creates research/writing-output artifacts through an expanded MCP server.
4. **Generated entities in ArangoDB.** Dossiers, revisions, and writing outputs become new collections.
5. **Unstructured Markdown round-trip.** The agent receives and returns only text files without a normative manifest/schema.

### Decision Outcome

Chosen option: a file-first provenance-gated workflow with the following rules:

1. **Evidence unit and identity.** Evidence is a full persisted chunk, not a 240-character retrieval snippet. Title-only, graph-only, and community rows remain leads until a concrete visible chunk is selected. Citation ID is derived from a canonical projection containing the version, source/canonical/document/chunk identity, normalized offsets, and excerpt SHA-256; raw/import run keys remain provenance metadata and do not destabilize identity across equivalent ingest runs.
2. **V5-only visibility.** The research pipeline explicitly selects `published_only` or `published_and_drafts` and applies the scope before candidate ranking, semantic hydration, graph expansion, and derived grouping. Legacy CLI/MCP calls retain current behavior. Stored derived context that may be tainted by a hidden draft is suppressed or recomputed over the visible subset.
3. **Immutable artifacts.** A dossier revision is a directory package with a canonical manifest, Markdown projection, and validation result under `data/generated/research/`. The package is completely written into a temporary sibling directory and atomically renamed. Every run gets a new revision ID; a deterministic content digest is stored separately.
4. **Curation lineage.** Include/exclude/pin are allowed only over the bounded candidate pool of a validated parent revision. Each non-empty curation creates a child revision with a parent ID and ordered operation log; in-place mutation is forbidden.
5. **External-agent round-trip.** `knowledge-base` creates a versioned JSON handoff with selected evidence, a citation allowlist, and an expected `output_kind`, whose allowed values are `draft` and `summary`. Creating any handoff requires explicit confirmation recorded as `egress_acknowledged=true`: the owner confirms that exact excerpts will be disclosed to the chosen external writing agent. If the evidence contains imported draft documents, a second independent confirmation, `draft_evidence_acknowledged=true`, is required. The external writing agent returns a versioned JSON writing-output package with the matching `output_kind`. The project does not call a model/network, store provider credentials, or add MCP writes.
6. **Untrusted import.** The incoming package is bounded by schema/version/bytes/counts, cannot instruct the validator to read a path or URL, must match the local handoff/dossier digest, and may reference only allowlisted evidence IDs. Unknown fields/citations, mismatched identity, or changed evidence reject the entire import.
7. **Honest validation.** Automatic checks report schema validity, package integrity, dossier freshness, citation resolution, and structural coverage separately. A writing-output section without citations must be marked `unsupported_by_corpus` with a reason. `human_reviewed` is never set automatically, and factual entailment is not promised.
8. **Sensitivity and owner review.** Dossiers, handoffs, and writing outputs contain exact excerpts and remain plaintext generated artifacts under the local OS trust boundary. Structured credentials, cookies, raw payload fields, and local archive/file paths are excluded from packages, but excerpts themselves may contain sensitive text or free-form secrets. The workflow does not promise secret-free excerpts: the owner must review selected evidence before acknowledgement and handoff.
9. **Filesystem boundary.** Package directories are created with POSIX mode `0700` and files with `0600` by default. The workflow refuses to write if any existing output-path component is a symlink. A custom output outside `data/generated/` requires a warning and a separate CLI confirmation, `--acknowledge-unsafe-output`, before writing.
10. **Acceptance gate.** Feature 007 is not complete until four independently recorded acceptance passes succeed: the dossier/citation/curation workflow, the writing-output round-trip with `output_kind=draft`, the writing-output round-trip with `output_kind=summary`, and the privacy/path-safety boundary. Success for one output kind or automated tests does not substitute for the remaining sections.

This ADR does not supersede ADR 0004–0008: MCP remains read-only, global legacy visibility does not change, the GraphRAG core remains extractive, and generated outputs remain non-authoritative.

### Consequences

- Good: every evidence fragment and writing-output citation can be verified down to a concrete normalized chunk and provenance.
- Good: immutable revisions and content digests provide reproducible research lineage without new DB entities.
- Good: the writing agent receives a minimally disclosed bounded handoff instead of DB credentials or the entire corpus.
- Good: V5 creates no breaking change for existing CLI/MCP consumers.
- Good: writing-agent unavailability does not block the extractive dossier/curation workflow.
- Bad: the user manually passes the handoff and returns the writing-output package; there is no one-command generation.
- Bad: directory packages and several JSON schemas increase contract and validation code.
- Bad: artifacts are not encrypted; local permissions and user care are the only protection.
- Neutral: citation coverage proves structure and resolvability, not semantic truth of the writing output.
- Neutral: exact historical replay remains impossible without parser/schema/code/config fingerprints; the content digest compares the available projection.
- Neutral: a separate future ADR is required for direct provider invocation, remote/shared workflows, MCP writes, encryption, or automatic factual evaluation.

### Review Plan

Revisit this decision if:

- the manual file round-trip becomes the main source of errors or unacceptable delay;
- a specific model/API must be called directly from `knowledge-base`;
- the writing agent must operate unattended, remotely, or for multiple users;
- generated artifacts require encryption, package signing, or a stronger identity/auth model;
- corpus scale breaks the 30-second dossier goal and requires persisted status-specific indexes;
- a full editable research workspace, concurrent curation, or conflict merging is required;
- automatic factual/entailment evaluation receives a separate measurable quality contract;
- query-time visibility must change globally for CLI/MCP rather than only for V5.

### Links

- [Feature 007 specification](../../specs/007-writer-research-workflow/spec.md)
- [Feature 007 implementation plan](../../specs/007-writer-research-workflow/plan.md)
- [ADR 0004: Local read-only MCP](0004-local-read-only-mcp-server-for-knowledge-base.md)
- [ADR 0005: Provenance and private archives](0005-define-source-provenance-and-private-archive-boundaries.md)
- [ADR 0006: Local security and privacy boundary](0006-define-the-local-security-and-privacy-trust-boundary.md)
- [ADR 0007: Rebuildable embeddings and extractive GraphRAG](0007-adopt-rebuildable-embeddings-and-extractive-graphrag.md)
- [ADR 0008: Offline visualization and graph export](0008-adopt-offline-visualization-and-graph-export.md)
