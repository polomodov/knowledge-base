# Follow-up аудит (июль 2026): план волн

**База планирования:** `main` после GraphRAG / viz / MCP / v5 runtime (`95cdb90` и новее).

**Режим:** узкие PR-волны; merge в `main` по одной.

**Статус эпика:** завершён 13 июля 2026 года. Все волны W1–W14 смерджены в `main`; последняя из них вошла через PR #58 (`ddb6211`). Активных implementation-волн в этом трекере не осталось.

**V5 acceptance (N1):** сам эпик выполнил только **подготовку** чеклистов/доков. На момент merge W14 13 июля 2026 года `acceptance.md` оставался `NOT RUN`, T050–T053 не были отмечены выполненными, а feature 007 не имела статуса Complete. Отдельная независимая приёмка завершилась 14 июля четырьмя `PASS`; evidence и итоговый peer audit записаны в [acceptance.md](../specs/007-writer-research-workflow/acceptance.md).

Предыдущий remediation (46 находок) закрыт — см. [implementation-audit-plan.md](implementation-audit-plan.md).

## Статус волн

| Волна | Тема | Ветка / PR | Статус |
|------:|------|------------|--------|
| W1 | Docs sync (AGENTS, Sonar claim) | [`docs/agents-docs-sync` / PR #45](https://github.com/polomodov/knowledge-base/pull/45) | ✅ merged (`9415bcd`) |
| W2 | CI `--vector-index` | [`fix/ci-vector-index` / PR #46](https://github.com/polomodov/knowledge-base/pull/46) | ✅ merged (`d7770ea`) |
| W3 | Retrieval degraded honesty | [`fix/retrieval-degraded-honesty` / PR #47](https://github.com/polomodov/knowledge-base/pull/47) | ✅ merged (`ca99b0f`) |
| W4 | Run lifecycle `error` | [`fix/run-lifecycle-error-status` / PR #48](https://github.com/polomodov/knowledge-base/pull/48) | ✅ merged (`62d0860`) |
| W5 | Stale derived indexes | [`fix/stale-derived-index-warnings` / PR #49](https://github.com/polomodov/knowledge-base/pull/49) | ✅ merged (`098e782`) |
| W6 | RU BM25 analyzer | [`fix/arangosearch-ru-analyzer` / PR #51](https://github.com/polomodov/knowledge-base/pull/51) | ✅ merged (`db93076`) |
| W7 | Language + Medium zip safety | [`fix/ingest-language-and-zip-safety` / PR #52](https://github.com/polomodov/knowledge-base/pull/52) | ✅ merged (`a08eb1c`) |
| W8 | Embedding ops profile | [`fix/embedding-ops-profile` / PR #50](https://github.com/polomodov/knowledge-base/pull/50) | ✅ merged (`719d6e9`) |
| W9 | Book-cube works extraction | [`feat/book-cube-works-extraction` / PR #53](https://github.com/polomodov/knowledge-base/pull/53) | ✅ merged (`ae9b1d9`) |
| W10 | CLI module split | [`refactor/cli-module-split` / PR #58](https://github.com/polomodov/knowledge-base/pull/58) | ✅ merged (`ddb6211`) |
| W11 | Research module boundaries | [`refactor/research-module-boundaries` / PR #57](https://github.com/polomodov/knowledge-base/pull/57) | ✅ merged (`0beb981`) |
| W12 | Viz public API + retrieval facade | [`refactor/viz-retrieval-boundaries` / PR #55](https://github.com/polomodov/knowledge-base/pull/55) | ✅ merged (`0dd8841`) |
| W13 | MCP/processed boundary docs | [`docs/mcp-research-boundary` / PR #54](https://github.com/polomodov/knowledge-base/pull/54) | ✅ merged (`a1789dd`) |
| W14 | V5 acceptance prep only | [`docs/v5-acceptance-prep` / PR #56](https://github.com/polomodov/knowledge-base/pull/56) | ✅ merged (`81d3cc9`) |

## W14 (prep only) — зафиксированный результат

- На момент merge в доках было явно: v5 runtime + automated gates ready; T050–T053 pending.
- Чеклист reviewer: [acceptance.md](../specs/007-writer-research-workflow/acceptance.md) §1–4 + [quickstart.md](../specs/007-writer-research-workflow/quickstart.md).
- В scope W14 **не входили**: запись PASS/FAIL в `acceptance.md`, `[x]` на T050–T053 и status Complete для feature 007.

Независимая приёмка была выполнена позднее отдельным этапом после закрытого follow-up эпика, а не как его незавершённая волна.

## Out of scope на момент эпика

- PASS/FAIL в `specs/007-writer-research-workflow/acceptance.md` и Complete для feature 007 оставались отдельным follow-up после независимого review; этот follow-up завершён 14 июля 2026 года.
- Новые source adapters, LLM-synthesis, managed ArangoDB, второй store в `data/processed/`.
