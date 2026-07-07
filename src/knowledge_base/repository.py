from __future__ import annotations

from typing import Any

from knowledge_base.arango import ArangoClient


class KnowledgeRepository:
    def __init__(self, client: ArangoClient) -> None:
        self.client = client

    def upsert(self, collection: str, document: dict[str, Any]) -> dict[str, Any]:
        # created_at is immutable: on update, keep the original first-seen value
        # instead of letting the incoming payload overwrite it (finding #11).
        query = """
        UPSERT { _key: @key }
        INSERT @document
        UPDATE MERGE(OLD, @document, HAS(OLD, "created_at") ? { created_at: OLD.created_at } : {})
        IN @@collection
        RETURN { old: OLD, new: NEW }
        """
        result = self.client.aql(query, {"@collection": collection, "key": document["_key"], "document": document})
        row = result[0]
        return {"created": row["old"] is None, "document": row["new"]}

    def upsert_edge(self, collection: str, edge: dict[str, Any]) -> dict[str, Any]:
        return self.upsert(collection, edge)

    def count(self, collection: str) -> int:
        query = "RETURN LENGTH(@@collection)"
        result = self.client.aql(query, {"@collection": collection})
        return int(result[0])
