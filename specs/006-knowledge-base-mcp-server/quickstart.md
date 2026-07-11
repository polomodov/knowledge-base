# Quickstart: Knowledge Base MCP Server

Поднимите ArangoDB runtime и загрузите данные:

```bash
uv run kb platform up
uv run kb platform bootstrap
uv run kb ingest fixture
uv run kb index rebuild --target all
```

Запустите MCP server локально через stdio:

```bash
cp config/pipeline.example.toml config/pipeline.local.toml
# Настройте embedding provider/model/dimension как у существующего индекса.
# Только для embedding.provider = "local":
uv pip install sentence-transformers
uv run --extra mcp kb-mcp --config config/pipeline.local.toml
```

Пример MCP client config:

```json
{
  "mcpServers": {
    "knowledge-base": {
      "command": "uv",
      "args": [
        "run",
        "--extra",
        "mcp",
        "kb-mcp",
        "--config",
        "/absolute/path/to/knowledge-base/config/pipeline.local.toml"
      ],
      "cwd": "/absolute/path/to/knowledge-base"
    }
  }
}
```

Запустите проверки:

```bash
uv run --extra dev pytest tests/unit
uv run --extra dev --extra mcp pytest tests/unit
KB_RUN_INTEGRATION=1 uv run --extra dev --extra mcp pytest tests/integration
uv run --extra dev ruff check src tests
uv run --extra dev mypy
npm run check:adr
```
