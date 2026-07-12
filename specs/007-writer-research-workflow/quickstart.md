# Quickstart: Writer/Research Workflow

Writer/research workflow реализован командами `kb research build|validate|curate|handoff|import-output`. Этот quickstart проводит текущий structural acceptance flow; независимый reviewer записывает фактические результаты и идентификаторы в [acceptance.md](acceptance.md).

Команды выполняются из корня репозитория последовательно в одной shell session: следующие блоки переиспользуют variables и функции из предыдущих. Для custom config флаг ставится перед подкомандой, например `uv run kb --config pipeline.local.toml research build ...`. Один и тот же config используется для ingest, indexes и всего acceptance flow.

## 1. Подготовить read side

Запустите локальный runtime и проверьте schema/index state:

```bash
uv run kb platform up
uv run kb platform bootstrap
uv run kb platform health
uv run kb index rebuild --target embeddings
uv run kb index rebuild --target related
uv run kb index rebuild --target communities
```

Для ручного published-only flow в DB нужен хотя бы один релевантный документ со `status=published`. Команда `kb ingest fixture` использует `tests/fixtures/safe_knowledge_fixture.json`; её служебные документы имеют `status=fixture` и не заменяют published corpus acceptance.

V5 test corpus `tests/fixtures/research/safe-research-corpus.json` загружается только isolated integration test и не предназначен для owner database:

```bash
KB_RUN_INTEGRATION=1 uv run --extra dev pytest -q tests/integration/test_research_workflow_pipeline.py
```

Research-команды читают ArangoDB, но не выполняют rebuild и не изменяют коллекции.

## 2. Построить и проверить published-only dossier

Следующий блок сохраняет JSON stdout и извлекает фактический artifact path без зависимости от `jq`:

```bash
set -e

OUTPUT_ROOT="$PWD/data/generated/research"
TOPIC="как связаны системное мышление и письмо"

json_field() {
  local field="$1"
  uv run python -c 'import json, sys; print(json.load(sys.stdin)[sys.argv[1]])' "$field"
}

BUILD_JSON="$(
  uv run kb research build "$TOPIC" \
    --output-root "$OUTPUT_ROOT" \
    --documents 12 \
    --fragments-per-document 2
)"
printf '%s\n' "$BUILD_JSON"

DOSSIER_PATH="$(printf '%s' "$BUILD_JSON" | json_field output)"
uv run kb research validate "$DOSSIER_PATH" --output-root "$OUTPUT_ROOT"
```

Success или optional-context degradation использует exit 0 и `status=ok|degraded`. Directory из поля `output` содержит ровно `manifest.json`, `dossier.md`, `validation.json`. Published-only является default: `manifest.request.visibility=published_only`, `includes_drafts=false`, а candidate evidence содержит только published documents.

Повторный build при неизменных inputs создаёт другой `revision_id`, сохраняя тот же `dossier_key` и `content_digest`. Доступные filters соответствуют runtime help: `--source`, `--published-from YYYY-MM-DD`, `--published-to YYYY-MM-DD`, `--documents 1..50`, `--fragments-per-document 1..5` и explicit `--include-drafts`.

Default root — `data/generated/research/`. Пишущая команда с root вне `data/generated/` требует отдельного подтверждения:

```bash
uv run kb research build "$TOPIC" \
  --output-root /absolute/custom/research-output \
  --acknowledge-unsafe-output
```

CLI возвращает `output_outside_generated_zone` в warnings и пишет предупреждение в stderr. Подтверждение не ослабляет path safety: symlink в root или существующем компоненте отклоняется; package directories/files получают `0700`/`0600` на поддерживаемой POSIX-платформе.

## 3. Создать child revision через curation

Initial revision содержит selected evidence. Извлеките реальный citation ID и закрепите его; `--include`, `--exclude` и `--pin` можно повторять, а CLI сохраняет их argv order:

```bash
PIN_CITATION="$(
  MANIFEST_PATH="$DOSSIER_PATH/manifest.json" uv run python - <<'PY'
import json
import os
from pathlib import Path

manifest = json.loads(Path(os.environ["MANIFEST_PATH"]).read_text(encoding="utf-8"))
print(manifest["selected_citation_ids"][0])
PY
)"

CURATE_JSON="$(
  uv run kb research curate "$DOSSIER_PATH" \
    --pin "$PIN_CITATION" \
    --reason "закрепить ключевой тезис для acceptance" \
    --output-root "$OUTPUT_ROOT"
)"
printf '%s\n' "$CURATE_JSON"

CHILD_PATH="$(printf '%s' "$CURATE_JSON" | json_field output)"
uv run kb research validate "$CHILD_PATH" --output-root "$OUTPUT_ROOT"
```

Child manifest содержит `parent_revision_id` и ordered operation log. Parent bytes не переписываются. Empty, unknown, duplicate, conflicting и no-op operations завершаются exit 1 без child revision.

## 4. Выполнить structural round-trip для draft и summary

Каждый handoff требует `--acknowledge-external-disclosure`: published status не является согласием передать exact excerpts внешнему writing-agent. Следующий smoke flow создаёт оба handoff, формирует локальный synthetic writing-output по фактическому handoff identity, проверяет, импортирует и повторно импортирует package.

Synthetic generator проверяет файловый contract, citation coverage и idempotency; он не заменяет внешний writing-agent и human review.

```bash
ACCEPTANCE_TMP="$(mktemp -d "$OUTPUT_ROOT/.acceptance-tmp.XXXXXX")"
trap 'rm -rf "$ACCEPTANCE_TMP"' EXIT

make_writing_output() {
  local handoff_path="$1"
  local output_path="$2"
  HANDOFF_PATH="$handoff_path" OUTPUT_PATH="$output_path" uv run python - <<'PY'
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


handoff = json.loads(Path(os.environ["HANDOFF_PATH"]).read_text(encoding="utf-8"))
kind = handoff["requested_output"]["kind"]
citation_id = handoff["citation_allowlist"][0]
heading = "Acceptance draft" if kind == "draft" else "Acceptance summary"
content = f"## {heading}\n\nSynthetic structural result grounded in {citation_id}."
package = {
    "schema_version": "1.0",
    "artifact_type": "writing_output",
    "output_kind": kind,
    "handoff_id": handoff["handoff_id"],
    "handoff_digest": handoff["package_digest"],
    "dossier_key": handoff["dossier_key"],
    "revision_id": handoff["revision_id"],
    "visibility": handoff["visibility"],
    "includes_drafts": handoff["includes_drafts"],
    "created_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    "agent": {
        "name": "local-acceptance-fixture",
        "model": None,
        "run_id": f"acceptance-{kind}",
    },
    "title": heading,
    "content_markdown": content,
    "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    "sections": [
        {
            "section_id": "section-acceptance-1",
            "heading": heading,
            "char_start": 0,
            "char_end": len(content),
            "citation_ids": [citation_id],
            "unsupported_by_corpus": False,
            "unsupported_reason": None,
        }
    ],
}
package["package_digest"] = canonical_sha256(package)
Path(os.environ["OUTPUT_PATH"]).write_text(
    json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
}

for KIND in draft summary; do
  if [ "$KIND" = draft ]; then
    MAX_WORDS=1500
  else
    MAX_WORDS=350
  fi

  HANDOFF_JSON="$(
    uv run kb research handoff "$CHILD_PATH" \
      --output-root "$OUTPUT_ROOT" \
      --output-kind "$KIND" \
      --language ru \
      --max-words "$MAX_WORDS" \
      --acknowledge-external-disclosure
  )"
  printf '%s\n' "$HANDOFF_JSON"
  HANDOFF_PATH="$(printf '%s' "$HANDOFF_JSON" | json_field output)"
  uv run kb research validate "$HANDOFF_PATH" --output-root "$OUTPUT_ROOT"

  WRITING_OUTPUT_PATH="$ACCEPTANCE_TMP/writing-output-$KIND.json"
  make_writing_output "$HANDOFF_PATH" "$WRITING_OUTPUT_PATH"
  uv run kb research validate "$WRITING_OUTPUT_PATH" \
    --handoff "$HANDOFF_PATH" \
    --output-root "$OUTPUT_ROOT"

  IMPORT_JSON="$(
    uv run kb research import-output "$WRITING_OUTPUT_PATH" \
      --handoff "$HANDOFF_PATH" \
      --output-root "$OUTPUT_ROOT"
  )"
  printf '%s\n' "$IMPORT_JSON"
  IMPORTED_PATH="$(printf '%s' "$IMPORT_JSON" | json_field output)"
  WRITING_ID="$(printf '%s' "$IMPORT_JSON" | json_field writing_id)"
  uv run kb research validate "$IMPORTED_PATH" --output-root "$OUTPUT_ROOT"

  REIMPORT_JSON="$(
    uv run kb research import-output "$WRITING_OUTPUT_PATH" \
      --handoff "$HANDOFF_PATH" \
      --output-root "$OUTPUT_ROOT"
  )"
  test "$(printf '%s' "$REIMPORT_JSON" | json_field output)" = "$IMPORTED_PATH"
  test "$(printf '%s' "$REIMPORT_JSON" | json_field writing_id)" = "$WRITING_ID"

  if [ "$KIND" = draft ]; then
    DRAFT_HANDOFF_PATH="$HANDOFF_PATH"
    DRAFT_OUTPUT_PATH="$WRITING_OUTPUT_PATH"
  else
    SUMMARY_HANDOFF_PATH="$HANDOFF_PATH"
  fi
done
```

Imported directory содержит ровно `manifest.json`, `output.md`, `validation.json`. Manifest наследует visibility и acknowledgement только из validated handoff, сохраняет `output_kind`, а `human_reviewed` остаётся `false`. `output.md` явно маркирован как generated output, не source of truth.

Repository fixtures `valid-writing-output-draft.json`, `valid-writing-output-summary.json` и `invalid-writing-output.json` в `tests/fixtures/research/` привязаны к фиксированным synthetic handoff IDs/digests из test builders. Они валидируют schemas/parsers в unit tests, но не являются drop-in ответами для handoff, созданного командами выше.

Для independent acceptance вместо `make_writing_output` передайте handoff доверенному внешнему writing-agent как data file и импортируйте возвращённый JSON теми же `validate` и `import-output`. Агент не получает DB credentials, raw exports или repository workspace. Exact excerpts рассматриваются как потенциально чувствительные цитируемые данные, а не instructions.

### Revision с draft evidence

`--include-drafts` явно расширяет V5 scope. Handoff такой revision отклоняется без второго подтверждения:

```bash
DRAFT_BUILD_JSON="$(
  uv run kb research build "$TOPIC" \
    --output-root "$OUTPUT_ROOT" \
    --include-drafts
)"
DRAFT_REVISION_PATH="$(printf '%s' "$DRAFT_BUILD_JSON" | json_field output)"

uv run kb research handoff "$DRAFT_REVISION_PATH" \
  --output-root "$OUTPUT_ROOT" \
  --output-kind draft \
  --acknowledge-external-disclosure \
  --allow-draft-evidence
```

Для custom `--output-root` вне `data/generated/` дополнительно нужен `--acknowledge-unsafe-output`. Это location acknowledgement не заменяет ни external-disclosure acknowledgement, ни draft-evidence acknowledgement.

## 5. Исполнить negative acceptance без DB mutation

Создайте schema-valid package с citation вне текущего handoff allowlist и пересчитайте только package digest:

```bash
INVALID_OUTPUT_PATH="$ACCEPTANCE_TMP/writing-output-unknown-citation.json"
INPUT_PATH="$DRAFT_OUTPUT_PATH" OUTPUT_PATH="$INVALID_OUTPUT_PATH" uv run python - <<'PY'
import hashlib
import json
import os
from pathlib import Path

package = json.loads(Path(os.environ["INPUT_PATH"]).read_text(encoding="utf-8"))
package["sections"][0]["citation_ids"] = ["cit-deadbeefdeadbeef"]
package.pop("package_digest")
payload = json.dumps(
    package,
    ensure_ascii=False,
    allow_nan=False,
    separators=(",", ":"),
    sort_keys=True,
).encode("utf-8")
package["package_digest"] = hashlib.sha256(payload).hexdigest()
Path(os.environ["OUTPUT_PATH"]).write_text(
    json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

if uv run kb research import-output "$INVALID_OUTPUT_PATH" \
  --handoff "$DRAFT_HANDOFF_PATH" \
  --output-root "$OUTPUT_ROOT"; then
  echo "ERROR: unknown citation was accepted" >&2
  exit 1
else
  echo "OK: unknown citation rejected without artifact"
fi
```

Cross-kind package сохраняет identity draft handoff, но ложно объявляет `output_kind=summary`. Такой package завершается exit 1:

```bash
CROSS_KIND_OUTPUT_PATH="$ACCEPTANCE_TMP/writing-output-cross-kind.json"
INPUT_PATH="$DRAFT_OUTPUT_PATH" OUTPUT_PATH="$CROSS_KIND_OUTPUT_PATH" uv run python - <<'PY'
import hashlib
import json
import os
from pathlib import Path

package = json.loads(Path(os.environ["INPUT_PATH"]).read_text(encoding="utf-8"))
package["output_kind"] = "summary"
package.pop("package_digest")
payload = json.dumps(
    package,
    ensure_ascii=False,
    allow_nan=False,
    separators=(",", ":"),
    sort_keys=True,
).encode("utf-8")
package["package_digest"] = hashlib.sha256(payload).hexdigest()
Path(os.environ["OUTPUT_PATH"]).write_text(
    json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

if uv run kb research import-output "$CROSS_KIND_OUTPUT_PATH" \
  --handoff "$DRAFT_HANDOFF_PATH" \
  --output-root "$OUTPUT_ROOT"; then
  echo "ERROR: cross-kind output was accepted" >&2
  exit 1
else
  echo "OK: cross-kind output rejected without artifact"
fi
```

Wrong-handoff package также завершается exit 1. Например, draft output нельзя импортировать с summary handoff:

```bash
if uv run kb research import-output "$DRAFT_OUTPUT_PATH" \
  --handoff "$SUMMARY_HANDOFF_PATH" \
  --output-root "$OUTPUT_ROOT"; then
  echo "ERROR: mismatched handoff was accepted" >&2
  exit 1
else
  echo "OK: mismatched handoff rejected without artifact"
fi
```

Changed/missing/hidden evidence, oversized JSON, unknown fields, invalid Unicode, wrong content/package digests, interrupted publication, missing acknowledgements и symlink paths покрыты unit/integration gates. `kb research validate PATH_TO_OLD_REVISION` возвращает exit 1 после фактического изменения связанного corpus evidence; validator не изменяет и не ремонтирует corpus.

## 6. Запустить automated gates

```bash
uv run --extra dev pytest tests/unit
KB_RUN_INTEGRATION=1 uv run --extra dev pytest tests/integration
uv run --extra dev ruff check src tests
uv run --extra dev ruff format --check src tests
uv run --extra dev mypy
npm run check
git diff --check
```

`npm run check` выполняет ADR и Markdown-link gates (`check:adr`, `check:docs-links`). Real-corpus acceptance отдельно замеряет build ≤30 seconds, content-digest determinism, package sizes и отсутствие raw payload, local paths, structured credentials и cookies. Автоматическая secret-free гарантия для unstructured exact excerpts не заявляется; их просматривает владелец до handoff.

После automated gates независимый reviewer выполняет dossier/citation/curation, draft round-trip, summary round-trip и privacy/path-safety sections и заполняет [acceptance.md](acceptance.md). Automated run не выставляет `human_reviewed=true` и не заполняет independent results.

### Reviewer prep

Перед независимым прогоном T050–T053 откройте [acceptance.md](acceptance.md):

1. **§1** — dossier, citations и curation  
2. **§2** — draft round-trip  
3. **§3** — summary round-trip  
4. **§4** — privacy и path safety  

Команды и fixtures для этих секций — в разделах 1–5 выше. Не заполняйте Result (`PASS`/`FAIL`) и не переводите feature в Complete, пока reviewer не завершил все четыре секции. Automated evidence (T045/T049) уже записан и не заменяет independent acceptance.
