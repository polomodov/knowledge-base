import urllib.request
from typing import Any

import pytest

from knowledge_base.arango import ArangoClient
from knowledge_base.config import Settings


def test_aql_applies_requested_batch_size_and_timeout_to_entire_cursor(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None, float]] = []
    responses = iter(
        [
            {"result": [{"value": 1}], "hasMore": True, "id": "cursor-1"},
            {"result": [{"value": 2}], "hasMore": False},
        ]
    )

    def request(
        self,
        method,
        path,
        *,
        database=None,
        body=None,
        expected=(200, 201, 202),
        timeout_seconds,
    ):
        calls.append((method, path, body, timeout_seconds))
        return next(responses)

    monkeypatch.setattr(ArangoClient, "request", request)
    client = ArangoClient(Settings(arango_database="test"))

    assert client.aql("RETURN @value", {"value": 1}, batch_size=25_000) == [{"value": 1}, {"value": 2}]
    assert calls[0] == (
        "POST",
        "/_api/cursor",
        {"query": "RETURN @value", "bindVars": {"value": 1}, "batchSize": 25_000},
        30.0,
    )
    assert calls[1][0:2] == ("PUT", "/_api/cursor/cursor-1")
    assert calls[1][3] == 30.0


def test_aql_allows_bounded_timeout_override(monkeypatch) -> None:
    timeouts: list[float] = []

    def request(
        self,
        method,
        path,
        *,
        database=None,
        body=None,
        expected=(200, 201, 202),
        timeout_seconds,
    ):
        timeouts.append(timeout_seconds)
        return {"result": [], "hasMore": False}

    monkeypatch.setattr(ArangoClient, "request", request)

    assert ArangoClient(Settings()).aql("RETURN 1", timeout_seconds=17.5) == []
    assert timeouts == [17.5]


def test_request_uses_short_default_timeout(monkeypatch) -> None:
    timeouts: list[float] = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self) -> bytes:
            return b"{}"

    class Opener:
        def open(self, request: urllib.request.Request, *, timeout: float):
            timeouts.append(timeout)
            return Response()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: Opener())

    assert ArangoClient(Settings()).server_version() == {}
    assert timeouts == [10.0]


def test_aql_rejects_non_positive_batch_size() -> None:
    with pytest.raises(ValueError, match="batch_size"):
        ArangoClient(Settings()).aql("RETURN 1", batch_size=0)


@pytest.mark.parametrize("timeout_seconds", [0.0, -1.0, float("nan"), float("inf"), 300.1])
def test_request_rejects_invalid_timeout(timeout_seconds: float) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        ArangoClient(Settings()).request("GET", "/_api/version", timeout_seconds=timeout_seconds)


@pytest.mark.parametrize("timeout_seconds", [0.0, -1.0, float("nan"), float("inf"), 300.1])
def test_aql_rejects_invalid_timeout(timeout_seconds: float) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        ArangoClient(Settings()).aql("RETURN 1", timeout_seconds=timeout_seconds)
