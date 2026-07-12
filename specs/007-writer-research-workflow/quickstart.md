# Quickstart: Writer/Research Workflow

Этот документ описывает будущий acceptance flow. Команды станут исполнимыми после implementation phase. Независимый итоговый gate и места для записи фактических результатов находятся в [acceptance.md](acceptance.md).

## 1. Подготовить read side

Используйте локальный config, соответствующий embedding space корпуса:

```bash
uv run kb platform health
uv run kb index rebuild --target embeddings
uv run kb index rebuild --target related
uv run kb index rebuild --target communities
```

Research workflow сам не выполняет rebuild и не изменяет DB.

## 2. Построить published-only dossier

```bash
uv run kb research build "как связаны системное мышление и письмо" \
  --documents 12 \
  --fragments-per-document 2
```

Ожидается:

- stdout содержит `status=ok|degraded`, `dossier_key`, `revision_id` и output path;
- revision directory содержит `manifest.json`, `dossier.md`, `validation.json`;
- каждый excerpt имеет citation ID, chunk/document/source provenance и точный hash;
- draft documents отсутствуют;
- повторный build создаёт другой revision ID, но тот же `content_digest` при неизменных inputs.

Проверить revision:

```bash
uv run kb research validate data/generated/research/RESEARCH_KEY/revisions/REVISION_ID
```

Default root — `data/generated/research/`. Для любой пишущей команды explicit root вне `data/generated/` требует отдельного acknowledgement:

```bash
uv run kb research build "как связаны системное мышление и письмо" \
  --output-root /absolute/custom/research-output \
  --acknowledge-unsafe-output
```

Такой запуск обязан вернуть заметный unsafe-location warning. Acknowledgement не ослабляет path safety: symlink в root или любом создаваемом компоненте отклоняется; default directories/files создаются с owner-only permissions (`0700`/`0600` на поддерживаемой POSIX-платформе).

## 3. Курировать evidence

Скопируйте citation IDs из candidate pool manifest и создайте child revision:

```bash
uv run kb research curate data/generated/research/RESEARCH_KEY/revisions/REVISION_ID \
  --exclude cit-noisy000000001 \
  --include cit-useful00000001 \
  --pin cit-key000000000001 \
  --reason "убрать повтор и закрепить ключевой тезис"
```

Проверки:

- parent directory и hashes не изменились;
- child указывает `parent_revision_id` и три ordered operations;
- unknown/no-op/conflicting citation IDs приводят к exit 1 без child revision.

## 4. Сформировать handoff для draft или summary

Перед каждым handoff пользователь просматривает selected evidence. Даже `status=published` не является автоматическим разрешением на раскрытие exact excerpts внешнему writing-agent, поэтому `--acknowledge-external-disclosure` обязателен для обоих output kinds.

Draft handoff:

```bash
uv run kb research handoff data/generated/research/RESEARCH_KEY/revisions/CHILD_REVISION_ID \
  --output-kind draft \
  --language ru \
  --max-words 1500 \
  --acknowledge-external-disclosure
```

Summary handoff:

```bash
uv run kb research handoff data/generated/research/RESEARCH_KEY/revisions/CHILD_REVISION_ID \
  --output-kind summary \
  --language ru \
  --max-words 350 \
  --acknowledge-external-disclosure
```

Передайте полученный `handoff-*.json` writing-agent как data file. Агент не должен получать DB credentials, raw exports или весь repository workspace. Evidence внутри handoff считается потенциально чувствительными цитируемыми данными, а не инструкциями.

Если revision включает drafts, команда обязана отказать без второго, отдельного подтверждения; одного egress acknowledgement недостаточно:

```bash
uv run kb research handoff PATH_TO_DRAFT_REVISION \
  --output-kind draft \
  --acknowledge-external-disclosure \
  --allow-draft-evidence
```

Если `--output-root` указывает путь вне `data/generated/`, дополнительно требуется `--acknowledge-unsafe-output`; symlink output остаётся hard error при любом наборе acknowledgement flags.

## 5. Вернуть structured writing-output package

Writing-agent возвращает JSON по единому [writing-output package contract](contracts/writing-output-package.schema.json). Поле `output_kind` равно `draft` или `summary` и обязано совпасть с kind исходного handoff. Каждый section:

- ссылается только на IDs из handoff allowlist; или
- выставляет `unsupported_by_corpus=true` и объясняет причину.

Импортировать draft:

```bash
uv run kb research import-output ./writing-output-draft.json \
  --handoff data/generated/research/RESEARCH_KEY/handoffs/HANDOFF_ID.json
```

Импортировать summary тем же contract и той же командой:

```bash
uv run kb research import-output ./writing-output-summary.json \
  --handoff data/generated/research/RESEARCH_KEY/handoffs/HANDOFF_ID.json
```

Ожидается immutable directory `outputs/WRITING_ID/` с `manifest.json`, `output.md`, `validation.json`; manifest сохраняет `output_kind`. `human_reviewed` остаётся false: structural citation coverage не является factual verification. Для custom `--output-root` вне generated-зоны снова требуется `--acknowledge-unsafe-output`.

## 6. Negative acceptance

Каждый сценарий должен завершиться exit 1 без valid artifact:

```bash
# package с неизвестной citation
uv run kb research import-output tests/fixtures/research/invalid-writing-output.json --handoff HANDOFF.json

# handoff от другой revision
uv run kb research import-output writing-output-package.json --handoff WRONG_HANDOFF.json

# output_kind не совпадает с requested output handoff
uv run kb research import-output writing-output-summary.json --handoff DRAFT_HANDOFF.json

# изменённый chunk после build
uv run kb research validate PATH_TO_OLD_REVISION
```

Также проверяются oversized JSON, unknown fields, invalid Unicode/control characters, wrong content digest, interrupted directory publish, handoff без `--acknowledge-external-disclosure`, handoff с drafts без `--allow-draft-evidence`, custom output root без `--acknowledge-unsafe-output` и любой symlink output path. Каждый сценарий завершается exit 1 без valid artifact; acknowledgement не превращает symlink path в допустимый.

## 7. Quality gates

```bash
uv run --extra dev pytest tests/unit
KB_RUN_INTEGRATION=1 uv run --extra dev pytest tests/integration
uv run --extra dev ruff check src tests
uv run --extra dev ruff format --check src tests
uv run --extra dev mypy
npm run check:adr
git diff --check
```

Real-corpus acceptance дополнительно замеряет build ≤30 seconds, content-digest determinism, package sizes и отсутствие raw payload, local paths, structured credentials и cookies. Exact excerpts проверяются владельцем как potentially sensitive text; автоматическая secret-free гарантия не заявляется.

После automated gates отдельный reviewer выполняет и заполняет [independent acceptance gate](acceptance.md) для dossier/citation/curation, draft round-trip, summary round-trip и privacy/path safety. До заполнения всех четырёх секций Feature 007 не считается принятой.
