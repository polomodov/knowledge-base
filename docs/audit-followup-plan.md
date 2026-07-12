# Follow-up аудит (июль 2026): план волн

**База:** `main` после GraphRAG / viz / MCP / v5 runtime (`95cdb90` и новее).  
**Режим:** узкие PR-волны; merge в `main` по одной.  
**V5 acceptance:** в этом эпике только подготовка чеклистов/доков; `acceptance.md` остаётся `NOT RUN`.

Предыдущий remediation (46 находок) закрыт — см. [implementation-audit-plan.md](implementation-audit-plan.md).

## Статус волн

| Волна | Тема | Ветка / PR | Статус |
|------:|------|------------|--------|
| W1 | Docs sync (AGENTS, Sonar claim) | `docs/agents-docs-sync` | in progress |
| W2 | CI `--vector-index` | — | pending |
| W3 | Retrieval degraded honesty | — | pending |
| W4 | Run lifecycle `error` | — | pending |
| W5 | Stale derived indexes | — | pending |
| W6 | RU BM25 analyzer | — | pending |
| W7 | Language + Medium zip safety | — | pending |
| W8 | Embedding ops profile | — | pending |
| W9 | Book-cube works extraction | — | pending |
| W10 | CLI module split | — | pending |
| W11 | Research module boundaries | — | pending |
| W12 | Viz public API + retrieval facade | — | pending |
| W13 | MCP/processed boundary docs | — | pending |
| W14 | V5 acceptance prep only | — | pending |

## Out of scope

- PASS/FAIL в `specs/007-writer-research-workflow/acceptance.md` и Complete для feature 007.
- Новые source adapters, LLM-synthesis, managed ArangoDB, второй store в `data/processed/`.
