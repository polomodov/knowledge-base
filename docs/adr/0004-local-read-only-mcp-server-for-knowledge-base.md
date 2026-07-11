# 0004. Локальный read-only MCP server для knowledge-base / Local read-only MCP server for knowledge-base

```json adr-meta
{
  "id": "0004",
  "titleRu": "Локальный read-only MCP server для knowledge-base",
  "titleEn": "Local read-only MCP server for knowledge-base",
  "status": "accepted",
  "date": "2026-07-06",
  "deciders": [
    "knowledge-base maintainer"
  ],
  "tags": [
    "mcp",
    "privacy",
    "retrieval",
    "integration"
  ],
  "supersedes": [],
  "supersededBy": []
}
```

## RU

### Контекст и проблема

`knowledge-base` уже умеет импортировать личные источники, хранить provenance и выполнять text/semantic/hybrid/local/global/graph retrieval через Python API и CLI. Следующий шаг - использовать эту базу в других локальных проектах и агентских инструментах без копирования retrieval-кода и без выдачи прав на ingest/index mutations.

MCP дает стандартный интерфейс для tools/resources/prompts, но сразу открывает архитектурную развилку: запускать сервер как локальный stdio-процесс или как HTTP endpoint, а также решать, какие операции безопасно exposed для внешних клиентов.

### Y-statement

В контексте использования personal knowledge-base из других локальных проектов и агентских клиентов, столкнувшись с privacy/auth рисками удаленного доступа и write operations, мы решили выбрать локальный read-only MCP server поверх stdio, чтобы безопасно открыть retrieval, documents, graph и provenance, принимая ограничение v1 на локальный запуск без HTTP/remote режима.

### Драйверы решения

- Личные raw-архивы и provenance содержат чувствительный контекст; remote endpoint требует отдельной auth/privacy модели.
- Другим проектам нужен стабильный agent-facing интерфейс, а не прямой импорт внутренних модулей.
- MCP v1 должен быть read-only, чтобы клиенты не могли случайно запустить ingest, index rebuild или export.
- Retrieval results должны сохранять `source_key`, document/chunk keys, URL и raw/import provenance.
- Реализация должна оставлять основной пакет работоспособным без MCP dependency.

### Рассмотренные варианты

- **Локальный read-only stdio MCP server.** Запускается как subprocess клиента, использует локальный config/ENV и открывает только retrieval tools/resources/prompts.
- **Streamable HTTP MCP service.** Удобен для нескольких процессов, но требует auth, сетевой политики, audit и защиты персональных данных.
- **Full pipeline MCP.** Дает ingest/index/export через MCP, но резко повышает риск случайных мутаций и смешивания runtime workflows.

### Итоговое решение

Выбран вариант: локальный read-only stdio MCP server.

v1 exposes только безопасные операции чтения: configured search, local/global GraphRAG, get normalized document, graph neighbors, source inventory и health. Raw snapshot payloads и локальные archive/file paths не переходят MCP-границу: document metadata и вложенный provenance проходят allowlist-проекции. Ingest, index rebuild, export и HTTP transport остаются вне v1.

### Последствия

- Хорошо: другие локальные агенты получают единый MCP-интерфейс к базе знаний без знания ArangoDB/CLI деталей.
- Хорошо: privacy boundary проще - сервер стартует локально, не публикует HTTP endpoint и не открывает write operations.
- Плохо: один MCP-сервер запускается как локальный процесс для каждого клиента; shared remote access не решается в v1.
- Нейтрально: будущий HTTP/remote режим требует отдельного ADR или обновления этого решения с auth/audit моделью.

### План пересмотра

Пересмотреть решение, если появится потребность подключать несколько удаленных клиентов, запускать MCP как shared service или управлять ingestion/indexing через agents.

### Ссылки

- [Model Context Protocol specification](https://modelcontextprotocol.io/specification/2025-11-25)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [MCP server tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)
- [MCP server resources](https://modelcontextprotocol.io/specification/2025-06-18/server/resources)

## EN

### Context and Problem Statement

`knowledge-base` can already ingest personal sources, preserve provenance, and run text/semantic/hybrid/local/global/graph retrieval through the Python API and CLI. The next step is to make that database usable from other local projects and agent tools without copying retrieval code and without granting clients mutation powers.

MCP provides a standard tools/resources/prompts interface, but it creates an architectural fork: run the server as a local stdio process or as an HTTP endpoint, and decide which operations are safe to expose.

### Y-statement

In the context of using the personal knowledge-base from other local projects and agent clients, facing privacy/auth risks around remote access and write operations, we decided for a local read-only MCP server over stdio to safely expose retrieval, documents, graph, and provenance, accepting the v1 limitation of local-only execution without HTTP/remote mode.

### Decision Drivers

- Personal raw archives and provenance contain sensitive context; remote endpoints need a separate auth/privacy model.
- Other projects need a stable agent-facing interface instead of direct imports of internal modules.
- MCP v1 must be read-only so clients cannot accidentally run ingest, index rebuild, or export.
- Retrieval results must preserve `source_key`, document/chunk keys, URL, and raw/import provenance.
- The base package should remain usable without the optional MCP dependency.

### Considered Options

- **Local read-only stdio MCP server.** Runs as a client subprocess, uses local config/ENV, and exposes only retrieval tools/resources/prompts.
- **Streamable HTTP MCP service.** Useful for multiple processes, but requires auth, network policy, audit, and personal data protection.
- **Full pipeline MCP.** Exposes ingest/index/export through MCP, but greatly increases mutation risk and workflow coupling.

### Decision Outcome

Chosen option: local read-only stdio MCP server.

v1 exposes only safe read operations: configured search, local/global GraphRAG, get normalized document, graph neighbors, source inventory, and health. Raw snapshot payloads and local archive/file paths do not cross the MCP boundary: document metadata and nested provenance use allowlist projections. Ingest, index rebuild, export, and HTTP transport are out of v1 scope.

### Consequences

- Good: other local agents get a single MCP interface to the knowledge-base without knowing ArangoDB/CLI details.
- Good: the privacy boundary is simpler: the server starts locally, publishes no HTTP endpoint, and exposes no write operations.
- Bad: each client starts its own local MCP server process; shared remote access is not solved in v1.
- Neutral: a future HTTP/remote mode requires a separate ADR or revision with an auth/audit model.

### Review Plan

Revisit if multiple remote clients, a shared MCP service, or agent-managed ingestion/indexing become required.

### Links

- [Model Context Protocol specification](https://modelcontextprotocol.io/specification/2025-11-25)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [MCP server tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)
- [MCP server resources](https://modelcontextprotocol.io/specification/2025-06-18/server/resources)
