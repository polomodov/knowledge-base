# CLI Contract: Writer/Research Workflow

Все команды возвращают один JSON object в stdout. Warnings пишутся также в stderr по существующему CLI convention. Ни одна команда не изменяет ArangoDB.

## `kb research build TOPIC`

Создаёт initial immutable dossier revision.

```bash
uv run kb research build "systems thinking and writing" \
  [--output-root data/generated/research] \
  [--acknowledge-unsafe-output] \
  [--source SOURCE_KEY] \
  [--published-from YYYY-MM-DD] \
  [--published-to YYYY-MM-DD] \
  [--documents 12] \
  [--fragments-per-document 2] \
  [--include-drafts]
```

Bounds:

- `TOPIC`: 1..1000 Unicode code points after trim;
- `--documents`: `1..50`, default `12`;
- `--fragments-per-document`: `1..5`, default `2`;
- selected evidence hard cap: `100`;
- candidate pool hard cap: `150`.

Default visibility is `published_only`. `--include-drafts` changes it to `published_and_drafts` and adds a privacy warning/banner, but does not alter legacy search/MCP defaults.

Success:

```json
{
  "status": "ok",
  "dossier_key": "research-systems-thinking-0123456789ab",
  "revision_id": "rev-20260712T120000Z-01234567",
  "content_digest": "<sha256>",
  "output": "data/generated/research/.../revisions/...",
  "evidence": 24,
  "candidates": 72,
  "includes_drafts": false,
  "warnings": []
}
```

`status="degraded"` remains exit 0 when citations are valid and only optional context degraded. `status="no_evidence"` or `status="error"` exits 1 and does not publish a finalized revision.

## `kb research validate ARTIFACT`

Validates a dossier revision directory, handoff JSON, incoming writing-output JSON or imported writing directory by `artifact_type`.

```bash
uv run kb research validate data/generated/research/.../revisions/REVISION_ID
```

Output includes the booleans and per-citation states from `validation-result.schema.json`. `valid` and `valid_with_warnings` exit 0; `invalid` exits 1. Validation never repairs or rewrites the target.

## `kb research curate REVISION`

Создаёт child revision из immutable parent без нового retrieval.

```bash
uv run kb research curate PATH_TO_REVISION \
  [--include CITATION_ID]... \
  [--exclude CITATION_ID]... \
  [--pin CITATION_ID]... \
  [--reason "owner note"] \
  [--output-root data/generated/research] \
  [--acknowledge-unsafe-output]
```

Rules:

- минимум одна non-no-op operation;
- `include` разрешает citation из parent candidate pool;
- `exclude`/`pin` требуют selected citation;
- parent должен пройти current citation validation;
- conflicting/unknown/hidden operations reject whole command;
- success publishes a new revision with `parent_revision_id`; parent files remain byte-identical.

## `kb research handoff REVISION`

Создаёт versioned JSON handoff для внешнего writing-agent.

```bash
uv run kb research handoff PATH_TO_REVISION \
  [--output-root data/generated/research] \
  [--output-kind draft|summary] \
  [--language ru] \
  [--style STYLE_HINT] \
  [--max-words N] \
  --acknowledge-external-disclosure \
  [--acknowledge-unsafe-output] \
  [--allow-draft-evidence]
```

Revision сначала валидируется. `--acknowledge-external-disclosure` обязателен для любого handoff: статус published не является согласием на передачу exact excerpts внешнему агенту. Если `includes_drafts=true`, отсутствие отдельного `--allow-draft-evidence` является hard error. Оба решения записываются в package как `egress_acknowledged` и `draft_evidence_acknowledged`.

Handoff содержит selected/pinned evidence, citation allowlist и instructions. Allowlist не переносит raw payload, structured config/env credentials, cookies или local archive paths; exact excerpts остаются potentially sensitive и должны быть просмотрены владельцем перед подтверждением.

Повторный вызов с тем же revision и одинаковыми output hints даёт тот же content-derived `handoff_id`; существующий byte-identical файл переиспользуется.

## `kb research import-output PACKAGE`

Проверяет structured writing-output package от writing-agent и атомарно публикует generated writing artifact. Один contract принимает `output_kind=draft|summary`; kind должен точно совпасть с `requested_output.kind` handoff.

```bash
uv run kb research import-output ./writing-output-package.json \
  --handoff data/generated/research/.../handoffs/HANDOFF_ID.json \
  [--output-root data/generated/research] \
  [--acknowledge-unsafe-output]
```

Validation order:

1. input byte limit and JSON shape;
2. supported schema/artifact type and no unknown fields;
3. `output_kind` equals the handoff request;
4. package/content digests;
5. exact handoff ID/digest and dossier revision match;
6. inherited visibility scope; acknowledgement state читается только из validated local handoff и копируется в imported manifest, а не принимается из incoming package;
7. section ranges and unique section IDs;
8. every citation belongs to handoff allowlist;
9. sections without citations have `unsupported_by_corpus=true` and a reason;
10. current dossier citation validation.

Unknown citations, mismatched handoff/dossier, changed evidence or structural errors reject the entire import (exit 1). Unsupported-but-explicit sections are accepted with warnings. Automatic validation does not claim factual entailment.

Success:

```json
{
  "status": "ok",
  "writing_id": "writing-0123456789abcdef",
  "output_kind": "summary",
  "dossier_key": "research-systems-thinking-0123456789ab",
  "revision_id": "rev-20260712T120000Z-01234567",
  "output": "data/generated/research/.../outputs/writing-0123456789abcdef",
  "citations_resolved": true,
  "coverage_complete": true,
  "unsupported_sections": 0,
  "human_reviewed": false,
  "warnings": []
}
```

## Output-zone policy

Default output lives under `data/generated/research/`, which is gitignored. Every command that writes artifacts accepts `--output-root`; a resolved root outside `data/generated/` is rejected unless the caller also passes `--acknowledge-unsafe-output`. An acknowledged custom root still produces a prominent stderr warning and a stable warning code in stdout.

Before creating or publishing files, the implementation checks every existing path component without following symlinks and rejects a symlink at any component or final target. On supported POSIX systems it creates package directories as `0700` and files as `0600`, verifies the effective modes, and uses a same-parent temporary directory plus atomic rename. Failure to establish this boundary is a hard error for the default root. Incoming package paths and URLs are data only and are never opened or fetched.

This location acknowledgement is separate from `--acknowledge-external-disclosure`: the latter is mandatory whenever exact excerpts are packaged for a writing-agent, even if the handoff file remains under the default root. Every package may contain sensitive exact excerpts even when drafts are excluded.

## Exit codes

| Exit | Meaning |
|------|---------|
| `0` | artifact created/reused or validation valid; warnings/degraded optional context may be present |
| `1` | invalid input, no grounded evidence, package/citation rejection, DB/read failure or atomic publish failure |
