# Follow-up аудит (июль 2026): план волн

**База:** `main` после GraphRAG / viz / MCP / v5 runtime (`95cdb90` и новее).

**Режим:** узкие PR-волны; merge в `main` по одной.

**V5 acceptance (N1):** в этом эпике только **подготовка** чеклистов/доков; `acceptance.md` остаётся `NOT RUN`; feature 007 не Complete.

Предыдущий remediation (46 находок) закрыт — см. [implementation-audit-plan.md](implementation-audit-plan.md).

## Статус волн

| Волна | Тема | Ветка / PR | Статус |
|------:|------|------------|--------|
| W1 | Docs sync (AGENTS, Sonar claim) | [`docs/agents-docs-sync`](https://github.com/polomodov/knowledge-base/pull/45) | open |
| W2 | CI `--vector-index` | [`fix/ci-vector-index`](https://github.com/polomodov/knowledge-base/pull/46) | open |
| W3 | Retrieval degraded honesty | [`fix/retrieval-degraded-honesty`](https://github.com/polomodov/knowledge-base/pull/47) | open |
| W4 | Run lifecycle `error` | [`fix/run-lifecycle-error-status`](https://github.com/polomodov/knowledge-base/pull/48) | open |
| W5 | Stale derived indexes | [`fix/stale-derived-index-warnings`](https://github.com/polomodov/knowledge-base/pull/49) | open |
| W6 | RU BM25 analyzer | [`fix/arangosearch-ru-analyzer`](https://github.com/polomodov/knowledge-base/pull/51) | open |
| W7 | Language + Medium zip safety | [`fix/ingest-language-and-zip-safety`](https://github.com/polomodov/knowledge-base/pull/52) | open |
| W8 | Embedding ops profile | [`fix/embedding-ops-profile`](https://github.com/polomodov/knowledge-base/pull/50) | open |
| W9 | Book-cube works extraction | [`feat/book-cube-works-extraction`](https://github.com/polomodov/knowledge-base/pull/53) | merged |
| W10 | CLI module split | [`refactor/cli-module-split`](https://github.com/polomodov/knowledge-base/pull/58) | open |
| W11 | Research module boundaries | [`refactor/research-module-boundaries`](https://github.com/polomodov/knowledge-base/pull/57) | open |
| W12 | Viz public API + retrieval facade | [`refactor/viz-retrieval-boundaries`](https://github.com/polomodov/knowledge-base/pull/55) | merged |
| W13 | MCP/processed boundary docs | [`docs/mcp-research-boundary`](https://github.com/polomodov/knowledge-base/pull/54) | merged |
| W14 | V5 acceptance prep only | [`docs/v5-acceptance-prep`](https://github.com/polomodov/knowledge-base/pull/56) | this PR |

## W14 (prep only) — что закрывает N1 как prep

- В доках явно: v5 runtime + automated gates ready; T050–T053 ещё pending.
- Чеклист reviewer: [acceptance.md](../specs/007-writer-research-workflow/acceptance.md) §1–4 + [quickstart.md](../specs/007-writer-research-workflow/quickstart.md).
- **Не** входит: запись PASS/FAIL в `acceptance.md`, `[x]` на T050–T053, status Complete для feature 007.

## Out of scope

- PASS/FAIL в `specs/007-writer-research-workflow/acceptance.md` и Complete для feature 007 (отдельный follow-up после независимого review).
- Новые source adapters, LLM-synthesis, managed ArangoDB, второй store в `data/processed/`.
