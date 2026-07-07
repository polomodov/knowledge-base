from __future__ import annotations

from typing import Any

from knowledge_base.arango import ArangoClient, ArangoError
from knowledge_base.constants import (
    DOCUMENT_COLLECTIONS,
    EDGE_COLLECTIONS,
    GRAPH_NAME,
    TEXT_VIEW_NAME,
    VECTOR_DIMENSION,
)


def bootstrap_schema(client: ArangoClient, *, embedding_dimension: int = VECTOR_DIMENSION) -> dict[str, Any]:
    client.ensure_database()
    collections = [_safe(lambda name=name: client.ensure_collection(name), name) for name in DOCUMENT_COLLECTIONS]
    edges = [_safe(lambda name=name: client.ensure_collection(name, edge=True), name) for name in EDGE_COLLECTIONS]
    indexes = _ensure_persistent_indexes(client)
    view = _safe(lambda: client.ensure_view(_text_view_body()), TEXT_VIEW_NAME)
    graph = _safe(lambda: client.ensure_graph(_graph_body()), GRAPH_NAME)
    vector = ensure_vector_index(client, dimension=embedding_dimension)
    return {
        "collections": collections,
        "edge_collections": edges,
        "indexes": indexes,
        "arangosearch_view": view,
        "graph": graph,
        "vector_index": vector,
    }


def health_report(client: ArangoClient) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        version = client.server_version()
        checks.append({"name": "arangodb", "status": "ok", "version": version.get("version")})
    except ArangoError as error:
        return {"status": "error", "checks": [{"name": "arangodb", "status": "error", "error": str(error)}]}

    for collection in [*DOCUMENT_COLLECTIONS, *EDGE_COLLECTIONS]:
        try:
            client.request("GET", f"/_api/collection/{collection}", database=client.settings.arango_database)
            checks.append({"name": f"collection:{collection}", "status": "ok"})
        except ArangoError as error:
            checks.append({"name": f"collection:{collection}", "status": "missing", "error": str(error)})

    checks.append(_check_view(client))
    checks.append(_check_graph(client))
    checks.append(_check_vector_index(client))

    return {
        "status": "ok" if all(check["status"] == "ok" for check in checks) else "degraded",
        "database": client.settings.arango_database,
        "checks": checks,
    }


def ensure_vector_index(client: ArangoClient, *, dimension: int = VECTOR_DIMENSION) -> dict[str, Any]:
    body = {
        "type": "vector",
        "name": "idx_chunks_embedding_vector",
        "fields": ["embedding"],
        "params": {
            "metric": "cosine",
            "dimension": dimension,
            "nLists": 1,
        },
    }
    result = _safe(lambda: client.ensure_index("chunks", body), "idx_chunks_embedding_vector")
    if result["status"] == "error":
        result["status"] = "degraded"
        result["note"] = "Vector index could not be created; semantic search should report degraded mode."
    return result


def _ensure_persistent_indexes(client: ArangoClient) -> list[dict[str, Any]]:
    definitions: list[tuple[str, dict[str, Any]]] = [
        ("sources", {"type": "persistent", "name": "idx_sources_type", "fields": ["type"]}),
        (
            "raw_snapshots",
            {"type": "persistent", "name": "idx_raw_source_sha", "fields": ["source_key", "sha256"], "unique": True},
        ),
        (
            "documents",
            {
                "type": "persistent",
                "name": "idx_documents_source_canonical",
                "fields": ["source_key", "canonical_id"],
                "unique": True,
            },
        ),
        ("chunks", {"type": "persistent", "name": "idx_chunks_document", "fields": ["document_key", "ordinal"]}),
        ("import_runs", {"type": "persistent", "name": "idx_import_runs_status", "fields": ["status", "started_at"]}),
        ("index_runs", {"type": "persistent", "name": "idx_index_runs_status", "fields": ["target", "status", "started_at"]}),
    ]
    return [_safe(lambda coll=coll, body=body: client.ensure_index(coll, body), body["name"]) for coll, body in definitions]


def _text_view_body() -> dict[str, Any]:
    return {
        "name": TEXT_VIEW_NAME,
        "type": "arangosearch",
        "links": {
            "documents": {
                "includeAllFields": False,
                "fields": {
                    "title": {"analyzers": ["text_en"]},
                    "text": {"analyzers": ["text_en"]},
                },
            },
            "chunks": {
                "includeAllFields": False,
                "fields": {
                    "text": {"analyzers": ["text_en"]},
                },
            },
            "topics": {
                "includeAllFields": False,
                "fields": {
                    "label": {"analyzers": ["text_en"]},
                    "description": {"analyzers": ["text_en"]},
                },
            },
            "works": {
                "includeAllFields": False,
                "fields": {
                    "title": {"analyzers": ["text_en"]},
                },
            },
        },
    }


def _graph_body() -> dict[str, Any]:
    return {
        "name": GRAPH_NAME,
        "edgeDefinitions": [
            {"collection": "document_from_source", "from": ["documents"], "to": ["sources"]},
            {"collection": "chunk_of_document", "from": ["chunks"], "to": ["documents"]},
            {"collection": "document_mentions_topic", "from": ["documents", "chunks"], "to": ["topics"]},
            {"collection": "document_mentions_author", "from": ["documents", "chunks"], "to": ["authors"]},
            {"collection": "document_references_work", "from": ["documents", "chunks"], "to": ["works"]},
            {"collection": "chunk_derived_from_raw", "from": ["chunks"], "to": ["raw_snapshots"]},
            {"collection": "item_related_to_item", "from": ["documents", "chunks"], "to": ["documents", "chunks"]},
        ],
        "orphanCollections": ["import_runs", "index_runs"],
    }


def _check_view(client: ArangoClient) -> dict[str, Any]:
    try:
        client.request("GET", f"/_api/view/{TEXT_VIEW_NAME}", database=client.settings.arango_database)
        return {"name": "arangosearch", "status": "ok", "view": TEXT_VIEW_NAME}
    except ArangoError as error:
        return {"name": "arangosearch", "status": "missing", "view": TEXT_VIEW_NAME, "error": str(error)}


def _check_graph(client: ArangoClient) -> dict[str, Any]:
    try:
        client.request("GET", f"/_api/gharial/{GRAPH_NAME}", database=client.settings.arango_database)
        return {"name": "graph", "status": "ok", "graph": GRAPH_NAME}
    except ArangoError as error:
        return {"name": "graph", "status": "missing", "graph": GRAPH_NAME, "error": str(error)}


def _check_vector_index(client: ArangoClient) -> dict[str, Any]:
    try:
        response = client.request("GET", "/_api/index?collection=chunks", database=client.settings.arango_database)
    except ArangoError as error:
        return {"name": "vector_index", "status": "missing", "index": "idx_chunks_embedding_vector", "error": str(error)}

    indexes = response.get("indexes", [])
    for index in indexes:
        if index.get("name") == "idx_chunks_embedding_vector" or index.get("type") == "vector":
            return {"name": "vector_index", "status": "ok", "index": index.get("name")}
    return {
        "name": "vector_index",
        "status": "degraded",
        "index": "idx_chunks_embedding_vector",
        "note": "Vector index is missing; semantic search uses stored embeddings fallback.",
    }


def _safe(operation, name: str) -> dict[str, Any]:
    try:
        result = operation()
        return {"name": name, "status": "ok", "created": bool(result.get("created", False))}
    except ArangoError as error:
        return {"name": name, "status": "error", "error": str(error)}
