from typing import Any

import pytest

from knowledge_base.arango import ArangoClient
from knowledge_base.config import Settings


def test_aql_applies_requested_batch_size_and_drains_cursor(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    responses = iter(
        [
            {"result": [{"value": 1}], "hasMore": True, "id": "cursor-1"},
            {"result": [{"value": 2}], "hasMore": False},
        ]
    )

    def request(self, method, path, *, database=None, body=None, expected=(200, 201, 202)):
        calls.append((method, path, body))
        return next(responses)

    monkeypatch.setattr(ArangoClient, "request", request)
    client = ArangoClient(Settings(arango_database="test"))

    assert client.aql("RETURN @value", {"value": 1}, batch_size=25_000) == [{"value": 1}, {"value": 2}]
    assert calls[0] == (
        "POST",
        "/_api/cursor",
        {"query": "RETURN @value", "bindVars": {"value": 1}, "batchSize": 25_000},
    )
    assert calls[1][0:2] == ("PUT", "/_api/cursor/cursor-1")


def test_aql_rejects_non_positive_batch_size() -> None:
    with pytest.raises(ValueError, match="batch_size"):
        ArangoClient(Settings()).aql("RETURN 1", batch_size=0)
