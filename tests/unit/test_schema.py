from knowledge_base.constants import VECTOR_DIMENSION
from knowledge_base.schema import ensure_vector_index


class _FakeClient:
    def __init__(self) -> None:
        self.index_bodies: list[tuple[str, dict]] = []

    def ensure_index(self, collection: str, body: dict) -> dict:
        self.index_bodies.append((collection, body))
        return {"created": True}


def test_ensure_vector_index_uses_configured_dimension() -> None:
    # The vector index dimension follows the configured embedding dimension rather than a
    # hardcoded constant, so a non-default dimension does not silently mismatch (finding #33).
    client = _FakeClient()
    result = ensure_vector_index(client, dimension=16)  # type: ignore[arg-type]

    assert result["status"] == "ok"
    collection, body = client.index_bodies[0]
    assert collection == "chunks"
    assert body["params"]["dimension"] == 16


def test_ensure_vector_index_defaults_to_vector_dimension() -> None:
    client = _FakeClient()
    ensure_vector_index(client)  # type: ignore[arg-type]
    assert client.index_bodies[0][1]["params"]["dimension"] == VECTOR_DIMENSION
