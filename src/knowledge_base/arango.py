from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from knowledge_base.config import Settings

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})
_warned_insecure_transport = False


def _warn_insecure_transport(url: str) -> None:
    # Basic auth is only base64-encoded; warn once if it would be sent in cleartext to a
    # non-loopback host over http (finding #40).
    global _warned_insecure_transport
    if _warned_insecure_transport:
        return
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "http" and (parsed.hostname or "") not in _LOOPBACK_HOSTS:
        _warned_insecure_transport = True
        sys.stderr.write(
            f"warning: sending ArangoDB Basic-auth credentials in cleartext over http to "
            f"{parsed.hostname}; use https for non-local hosts.\n",
        )


class ArangoError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


@dataclass(frozen=True)
class ArangoClient:
    settings: Settings

    def request(
        self,
        method: str,
        path: str,
        *,
        database: str | None = None,
        body: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200, 201, 202),
    ) -> Any:
        url = self._url(path, database=database)
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method.upper())
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        _warn_insecure_transport(self.settings.arango_url)
        token = f"{self.settings.arango_user}:{self.settings.arango_password}".encode("utf-8")
        request.add_header("Authorization", f"Basic {base64.b64encode(token).decode('ascii')}")

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=10) as response:
                payload = response.read().decode("utf-8")
                if response.status not in expected:
                    raise ArangoError(
                        f"Unexpected ArangoDB status {response.status} for {method} {path}",
                        status=response.status,
                        payload=payload,
                    )
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as error:
            payload = error.read().decode("utf-8")
            parsed = _parse_json(payload)
            if error.code in expected:
                return parsed
            message = parsed.get("errorMessage") if isinstance(parsed, dict) else payload
            raise ArangoError(
                f"ArangoDB HTTP {error.code}: {message}",
                status=error.code,
                payload=parsed,
            ) from error
        except urllib.error.URLError as error:
            raise ArangoError(f"Cannot reach ArangoDB at {self.settings.arango_url}: {error.reason}") from error

    def server_version(self) -> dict[str, Any]:
        return self.request("GET", "/_api/version")

    def ensure_database(self) -> dict[str, Any]:
        try:
            return self.request("POST", "/_api/database", body={"name": self.settings.arango_database})
        except ArangoError as error:
            if error.status == 409:
                return {"result": True, "created": False}
            raise

    def ensure_collection(self, name: str, *, edge: bool = False) -> dict[str, Any]:
        body = {"name": name, "type": 3 if edge else 2}
        try:
            result = self.request("POST", "/_api/collection", database=self.settings.arango_database, body=body)
            result["created"] = True
            return result
        except ArangoError as error:
            if error.status == 409:
                return {"name": name, "created": False}
            raise

    def ensure_index(self, collection: str, body: dict[str, Any]) -> dict[str, Any]:
        path = f"/_api/index?collection={urllib.parse.quote(collection)}"
        try:
            result = self.request("POST", path, database=self.settings.arango_database, body=body)
            result["created"] = bool(result.get("isNewlyCreated", result.get("created", True)))
            return result
        except ArangoError as error:
            if error.status == 409:
                return {"name": body.get("name"), "created": False}
            raise

    def ensure_view(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self.request("POST", "/_api/view", database=self.settings.arango_database, body=body)
            result["created"] = True
            return result
        except ArangoError as error:
            if error.status == 409:
                name = body["name"]
                result = self.request(
                    "PUT",
                    f"/_api/view/{urllib.parse.quote(name)}/properties",
                    database=self.settings.arango_database,
                    body={"links": body.get("links", {})},
                )
                result["created"] = False
                result["updated"] = True
                return result
            raise

    def ensure_graph(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self.request("POST", "/_api/gharial", database=self.settings.arango_database, body=body)
            result["created"] = True
            return result
        except ArangoError as error:
            if error.status == 409:
                return {"name": body.get("name"), "created": False}
            raise

    def aql(self, query: str, bind_vars: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        response = self.request(
            "POST",
            "/_api/cursor",
            database=self.settings.arango_database,
            body={"query": query, "bindVars": bind_vars or {}},
        )
        results = list(response.get("result", []))
        while response.get("hasMore"):
            response = self.request(
                "PUT",
                f"/_api/cursor/{response['id']}",
                database=self.settings.arango_database,
            )
            results.extend(response.get("result", []))
        return results

    def _url(self, path: str, *, database: str | None = None) -> str:
        if database:
            path = f"/_db/{urllib.parse.quote(database)}{path}"
        return f"{self.settings.arango_url}{path}"


def _parse_json(payload: str) -> Any:
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return payload
