# Data Model: Knowledge Base MCP Server

## MCPSearchResult

- `id`: underlying Arango document/chunk id
- `kind`: result kind, e.g. `document`, `chunk` or graph entity kind
- `title`, `snippet`, `score`, `score_components`
- `document_key`, `chunk_key`
- `resource_uri`: `kb://documents/{document_key}`
- `url`
- `provenance`: source/import/raw metadata

## MCPLocalContext

- `seeds`: formatted `MCPSearchResult` rows
- `entities`: allowlisted topic/author/work references
- `related_documents`: document references with similarity weight, resource URI and provenance
- `communities`: community metadata for seed documents

## MCPGlobalContext

- `communities`: ranked community summaries
- `communities[].documents`: cited document references with score, resource URI and provenance

## MCPDocument

- `document_key`, `resource_uri`
- `title`, `url`, `published_at`, `source_key`
- `text`, `truncated`
- `metadata`: allowlisted normalized metadata only; archive refs, attachment local paths and other raw-local structure are excluded
- `provenance`: source key, raw snapshot key, import run key, URL, Medium post metadata

## MCPSources

- `source_key`, `display_name`, `type`, `url`
- `document_count`
- `metadata`

## Resources

- `kb://sources`: JSON representation of `MCPSources`
- `kb://documents/{document_key}`: Markdown representation of `MCPDocument` with provenance header
