# CLI Contract: Knowledge Base MCP Server

## `kb-mcp --config PATH`

Starts a local read-only MCP server over stdio.

```bash
uv run --extra mcp kb-mcp --config config/pipeline.local.toml
```

Local config must use the same embedding provider/model/dimension as the indexed corpus; `pipeline.example.toml` is the hash/fixture template.
When `embedding.provider = "local"`, install the intentionally-unlocked `sentence-transformers` dependency in the server environment before startup.

Transport:

- v1 uses stdio only.
- HTTP/remote transport is out of scope.

## Tools

### `kb_search(query, mode="hybrid", source_key=null, limit=5, community_limit=5)`

Returns agent-ready search results with snippets and provenance.

Allowed modes:

- `text`
- `semantic`
- `hybrid`
- `local`
- `global`

`semantic`, `hybrid`, `local` and `global` use the configured embedding provider/model/dimension and `retrieval.min_similarity`. `community_limit` applies to `global`; both limits are clamped to `1..20`.

### `kb_get_document(document_key, max_chars=12000)`

Returns bounded normalized document content, a `truncated` flag and provenance. Raw snapshot payloads and local archive/file paths are not returned.

### `kb_graph_neighbors(start_type, key, source_key=null, documents_only=true, limit=10)`

Returns graph neighbors for `topic`, `author`, `work`, `document` or `chunk`.

### `kb_list_sources()`

Returns known sources and approximate document counts.

### `kb_health()`

Returns ArangoDB/schema health.

## Resources

- `kb://sources`
- `kb://documents/{document_key}`

## Prompt

- `research_knowledge_base(topic, source_key=null)`
