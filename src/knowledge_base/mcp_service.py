from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from knowledge_base.arango import ArangoError
from knowledge_base.config import Settings
from knowledge_base.embeddings import EmbeddingProviderError, build_embedding_provider
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.retrieval import (
    global_search,
    graph_neighbors,
    hybrid_search,
    local_search,
    semantic_search,
    text_search,
)
from knowledge_base.schema import health_report

SEARCH_MODES = {"text", "semantic", "hybrid", "local", "global"}
GRAPH_START_TYPES = {"topic", "author", "work", "document", "chunk"}
MIN_LIMIT = 1
MAX_LIMIT = 20
DEFAULT_SEARCH_LIMIT = 5
DEFAULT_COMMUNITY_LIMIT = 5
DEFAULT_GRAPH_LIMIT = 10
MIN_DOCUMENT_CHARS = 1_000
MAX_DOCUMENT_CHARS = 50_000
DEFAULT_DOCUMENT_CHARS = 12_000
DOCUMENT_URI_RE = re.compile(r"^kb://documents/(?P<document_key>[A-Za-z0-9_.:-]+)$")


@dataclass(frozen=True)
class KnowledgeBaseMCPService:
    repository: KnowledgeRepository
    settings: Settings

    def search(
        self,
        query: str,
        *,
        mode: str = "hybrid",
        source_key: str | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
        community_limit: int = DEFAULT_COMMUNITY_LIMIT,
    ) -> dict[str, Any]:
        normalized_mode = mode.lower().strip()
        normalized_limit = clamp_int(limit, minimum=MIN_LIMIT, maximum=MAX_LIMIT, default=DEFAULT_SEARCH_LIMIT)
        normalized_community_limit = clamp_int(
            community_limit,
            minimum=MIN_LIMIT,
            maximum=MAX_LIMIT,
            default=DEFAULT_COMMUNITY_LIMIT,
        )
        if normalized_mode not in SEARCH_MODES:
            return _error("invalid_mode", f"mode must be one of: {', '.join(sorted(SEARCH_MODES))}")

        try:
            if normalized_mode == "text":
                payload = text_search(self.repository, query, limit=normalized_limit, source_key=source_key)
            else:
                provider = build_embedding_provider(self.settings)
                if normalized_mode == "semantic":
                    payload = semantic_search(
                        self.repository,
                        query,
                        limit=normalized_limit,
                        source_key=source_key,
                        provider=provider,
                        min_similarity=self.settings.retrieval_min_similarity,
                    )
                elif normalized_mode == "hybrid":
                    payload = hybrid_search(
                        self.repository,
                        query,
                        limit=normalized_limit,
                        source_key=source_key,
                        provider=provider,
                        min_similarity=self.settings.retrieval_min_similarity,
                    )
                elif normalized_mode == "local":
                    payload = local_search(
                        self.repository,
                        query,
                        limit=normalized_limit,
                        source_key=source_key,
                        provider=provider,
                        min_similarity=self.settings.retrieval_min_similarity,
                    )
                else:
                    payload = global_search(
                        self.repository,
                        query,
                        limit=normalized_limit,
                        community_limit=normalized_community_limit,
                        source_key=source_key,
                        provider=provider,
                        min_similarity=self.settings.retrieval_min_similarity,
                    )
        except EmbeddingProviderError as error:
            return _error("embedding_provider_error", str(error))
        except ArangoError as error:
            return _arango_error(error)

        response = {
            "status": payload.get("status", "ok"),
            "mode": payload.get("mode", normalized_mode),
            "query": payload.get("query", query),
            "degraded_components": payload.get("degraded_components", []),
        }
        if normalized_mode in {"text", "semantic", "hybrid"}:
            response["results"] = [_format_result(result) for result in payload.get("results", [])]
        elif normalized_mode == "local":
            response.update(
                {
                    "seeds": [_format_result(result) for result in payload.get("seeds", [])],
                    "entities": [_format_entity(result) for result in payload.get("entities", [])],
                    "related_documents": [_format_document_reference(result) for result in payload.get("related_documents", [])],
                    "communities": [_format_community(result) for result in payload.get("communities", [])],
                },
            )
        else:
            response["communities"] = [_format_community(result) for result in payload.get("communities", [])]
        return response

    def get_document(self, document_key: str, *, max_chars: int = DEFAULT_DOCUMENT_CHARS) -> dict[str, Any]:
        normalized_max_chars = clamp_int(
            max_chars,
            minimum=MIN_DOCUMENT_CHARS,
            maximum=MAX_DOCUMENT_CHARS,
            default=DEFAULT_DOCUMENT_CHARS,
        )
        try:
            row = _document_row(self.repository, document_key)
        except ArangoError as error:
            return _arango_error(error)

        if row is None:
            return _error("document_not_found", f"Document not found: {document_key}", status="not_found")

        document = row["document"]
        text = document.get("text") or ""
        truncated = len(text) > normalized_max_chars
        return {
            "status": "ok",
            "document_key": document["_key"],
            "resource_uri": document_resource_uri(document["_key"]),
            "title": document.get("title"),
            "url": document.get("url"),
            "published_at": document.get("published_at"),
            "source_key": document.get("source_key"),
            "text": text[:normalized_max_chars],
            "truncated": truncated,
            "metadata": _format_document_metadata(document.get("metadata")),
            "provenance": _document_provenance(row),
        }

    def graph_neighbors(
        self,
        *,
        start_type: str,
        key: str,
        source_key: str | None = None,
        documents_only: bool = True,
        limit: int = DEFAULT_GRAPH_LIMIT,
    ) -> dict[str, Any]:
        normalized_start_type = start_type.lower().strip()
        normalized_limit = clamp_int(limit, minimum=MIN_LIMIT, maximum=MAX_LIMIT, default=DEFAULT_GRAPH_LIMIT)
        if normalized_start_type not in GRAPH_START_TYPES:
            return _error(
                "invalid_start_type",
                f"start_type must be one of: {', '.join(sorted(GRAPH_START_TYPES))}",
            )

        try:
            payload = _graph_neighbors(
                self.repository,
                start_type=normalized_start_type,
                key=key,
                source_key=source_key,
                documents_only=documents_only,
                limit=normalized_limit,
            )
        except ArangoError as error:
            return _arango_error(error)

        return {
            "status": payload.get("status", "ok"),
            "mode": payload.get("mode", "graph"),
            "query": payload.get("query"),
            "documents_only": documents_only,
            "results": [_format_result(result) for result in payload.get("results", [])],
        }

    def list_sources(self) -> dict[str, Any]:
        try:
            rows = self.repository.client.aql(
                """
                FOR source IN sources
                  LET document_count = LENGTH((
                    FOR document IN documents
                      FILTER document.source_key == source._key
                      RETURN 1
                  ))
                  SORT source._key ASC
                  RETURN {
                    source_key: source._key,
                    display_name: source.display_name,
                    type: source.type,
                    url: source.url,
                    document_count: document_count,
                    metadata: source.metadata
                  }
                """,
            )
        except ArangoError as error:
            return _arango_error(error)
        return {"status": "ok", "resource_uri": "kb://sources", "sources": rows}

    def health(self) -> dict[str, Any]:
        try:
            return health_report(self.repository.client)
        except ArangoError as error:
            return _arango_error(error)

    def document_resource(self, document_key: str) -> str:
        document = self.get_document(document_key, max_chars=MAX_DOCUMENT_CHARS)
        return document_to_markdown(document)

    def sources_resource(self) -> str:
        return json.dumps(self.list_sources(), ensure_ascii=False, indent=2)


def clamp_int(value: int | str | None, *, minimum: int, maximum: int, default: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def document_resource_uri(document_key: str | None) -> str | None:
    if not document_key:
        return None
    return f"kb://documents/{document_key}"


def document_key_from_uri(uri: str) -> str | None:
    match = DOCUMENT_URI_RE.match(uri)
    return match.group("document_key") if match else None


def document_to_markdown(payload: dict[str, Any]) -> str:
    if payload.get("status") != "ok":
        return json.dumps(payload, ensure_ascii=False, indent=2)

    provenance = payload.get("provenance") or {}
    header = [
        f"# {payload.get('title') or payload.get('document_key')}",
        "",
        f"- document_key: `{payload.get('document_key')}`",
        f"- source_key: `{payload.get('source_key')}`",
        f"- raw_snapshot_key: `{provenance.get('raw_snapshot_key')}`",
        f"- import_run_key: `{provenance.get('import_run_key')}`",
    ]
    if payload.get("url"):
        header.append(f"- url: {payload['url']}")
    if payload.get("published_at"):
        header.append(f"- published_at: {payload['published_at']}")
    medium_post = provenance.get("medium_post")
    if medium_post:
        header.append(f"- medium_post_id: `{medium_post.get('post_id')}`")
    if payload.get("truncated"):
        header.append("- truncated: true")
    header.extend(["", payload.get("text") or ""])
    return "\n".join(header)


def research_prompt(topic: str, source_key: str | None = None) -> str:
    source_line = (
        f'Use source_key="{source_key}" unless the user asks otherwise.'
        if source_key
        else ("Use source filters only when the user explicitly asks for a specific source.")
    )
    return "\n".join(
        [
            f"Research topic: {topic}",
            "",
            "Use the knowledge-base MCP tools in this order:",
            '1. Start with kb_search(mode="hybrid", limit=5).',
            '2. Use kb_search(mode="local") for a focused graph context or mode="global" for corpus themes.',
            "3. Expand only the most relevant cited results with kb_get_document.",
            "4. Use kb_graph_neighbors when a specific author/topic/work relationship matters.",
            "5. Cite source_key, document_key, URL and raw_snapshot_key in the answer.",
            source_line,
            "Do not treat generated summaries as sources of truth unless they cite original documents.",
        ],
    )


def _format_result(result: dict[str, Any]) -> dict[str, Any]:
    document_key = result.get("document_key")
    provenance = _format_provenance(result.get("provenance"))
    formatted = {
        "id": result.get("id"),
        "kind": result.get("kind", "document" if document_key else "result"),
        "title": result.get("title") or result.get("label"),
        "snippet": result.get("snippet"),
        "score": result.get("score"),
        "score_components": result.get("score_components") or {},
        "document_key": document_key,
        "chunk_key": result.get("chunk_key"),
        "resource_uri": document_resource_uri(document_key),
        "url": provenance.get("url"),
        "provenance": provenance,
    }
    if "graph_expanded" in result:
        formatted["graph_expanded"] = bool(result["graph_expanded"])
    return formatted


def _format_document_reference(result: dict[str, Any]) -> dict[str, Any]:
    document_key = result.get("document_key")
    provenance = _format_provenance(result.get("provenance"))
    formatted = {
        "document_key": document_key,
        "title": result.get("title"),
        "resource_uri": document_resource_uri(document_key),
        "url": provenance.get("url"),
        "provenance": provenance,
    }
    for key in ("score", "weight"):
        if key in result:
            formatted[key] = result[key]
    return formatted


def _format_community(result: dict[str, Any]) -> dict[str, Any]:
    formatted = {
        "community_key": result.get("community_key"),
        "summary": result.get("summary"),
    }
    for key in ("size", "top_topics", "score", "matched_documents", "seed_members"):
        if key in result:
            formatted[key] = result[key]
    if "documents" in result:
        formatted["documents"] = [_format_document_reference(document) for document in result.get("documents", [])]
    return formatted


def _format_entity(result: dict[str, Any]) -> dict[str, Any]:
    formatted = {
        "id": result.get("id"),
        "kind": result.get("kind"),
        "label": result.get("label"),
    }
    if "documents" in result:
        formatted["documents"] = result["documents"]
    return formatted


def _format_provenance(value: Any) -> dict[str, Any]:
    provenance = value if isinstance(value, dict) else {}
    return {
        "source_key": provenance.get("source_key"),
        "raw_snapshot_key": provenance.get("raw_snapshot_key"),
        "import_run_key": provenance.get("import_run_key"),
        "medium_post": _format_medium_post(provenance.get("medium_post")),
        "url": provenance.get("url"),
        "captured_at": provenance.get("captured_at"),
    }


def _format_document_metadata(value: Any) -> dict[str, Any]:
    metadata = value if isinstance(value, dict) else {}
    formatted = {
        key: metadata[key]
        for key in (
            "status",
            "guid",
            "feed_item_type",
            "message_id",
            "data_post",
            "snapshot_type",
            "fixture",
            "safe_for_git",
            "raw_snapshot_key",
        )
        if key in metadata
    }
    if isinstance(metadata.get("tags"), list):
        formatted["tags"] = [tag for tag in metadata["tags"] if isinstance(tag, str)]
    if isinstance(metadata.get("author"), str):
        formatted["author"] = metadata["author"]
    medium_post = _format_medium_post(metadata.get("medium_post"))
    if medium_post:
        formatted["medium_post"] = medium_post
    return formatted


def _format_medium_post(value: Any) -> dict[str, Any] | None:
    medium_post = value if isinstance(value, dict) else {}
    formatted = {
        key: medium_post[key]
        for key in (
            "post_id",
            "canonical_url",
            "medium_url",
            "post_sha256",
            "size_bytes",
            "exported_at",
            "raw_snapshot_key",
        )
        if key in medium_post
    }
    return formatted or None


def _graph_neighbors(
    repository: KnowledgeRepository,
    *,
    start_type: str,
    key: str,
    source_key: str | None,
    documents_only: bool,
    limit: int,
) -> dict[str, Any]:
    if start_type == "topic":
        return graph_neighbors(
            repository,
            topic=key,
            source_key=source_key,
            documents_only=documents_only,
            limit=limit,
        )
    if start_type == "author":
        return graph_neighbors(
            repository,
            author=key,
            source_key=source_key,
            documents_only=documents_only,
            limit=limit,
        )
    if start_type == "work":
        return graph_neighbors(
            repository,
            work=key,
            source_key=source_key,
            documents_only=documents_only,
            limit=limit,
        )
    if start_type == "document":
        return graph_neighbors(
            repository,
            document=key,
            source_key=source_key,
            documents_only=documents_only,
            limit=limit,
        )
    return graph_neighbors(
        repository,
        chunk=key,
        source_key=source_key,
        documents_only=documents_only,
        limit=limit,
    )


def _document_row(repository: KnowledgeRepository, document_key: str) -> dict[str, Any] | None:
    rows = repository.client.aql(
        """
        LET document = DOCUMENT("documents", @document_key)
        FILTER document != null
        LET anchor_chunk = FIRST(
          FOR chunk IN chunks
            FILTER chunk.document_key == document._key
            SORT chunk.ordinal ASC
            LIMIT 1
            RETURN chunk
        )
        LET raw_edge = anchor_chunk ? FIRST(
          FOR edge IN chunk_derived_from_raw
            FILTER edge._from == anchor_chunk._id
            LIMIT 1
            RETURN edge
        ) : null
        LET raw_document = raw_edge ? DOCUMENT(raw_edge._to) : null
        LET raw = raw_document == null ? null : {
          _key: raw_document._key,
          captured_at: raw_document.captured_at
        }
        LET source_edge = FIRST(
          FOR edge IN document_from_source
            FILTER edge._from == document._id
            LIMIT 1
            RETURN edge
        )
        RETURN {
          document: document,
          raw: raw,
          raw_edge: raw_edge,
          source_edge: source_edge
        }
        """,
        {"document_key": document_key},
    )
    return rows[0] if rows else None


def _document_provenance(row: dict[str, Any]) -> dict[str, Any]:
    document = row["document"]
    raw = row.get("raw")
    raw_edge = row.get("raw_edge")
    source_edge = row.get("source_edge")
    source_provenance = (source_edge or {}).get("provenance") or {}
    return {
        "source_key": document.get("source_key"),
        "raw_snapshot_key": raw.get("_key") if raw else source_provenance.get("raw_snapshot_key"),
        "import_run_key": (raw_edge or {}).get("import_run_key") or (source_edge or {}).get("import_run_key"),
        "medium_post": _format_medium_post(source_provenance.get("medium_post")),
        "url": document.get("url"),
        "captured_at": raw.get("captured_at") if raw else None,
    }


def _error(error: str, message: str, *, status: str = "error") -> dict[str, Any]:
    return {"status": status, "error": error, "message": message}


def _arango_error(error: ArangoError) -> dict[str, Any]:
    return {
        "status": "error",
        "error": "database_error",
        "message": str(error),
    }
