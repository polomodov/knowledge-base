from knowledge_base.constants import VECTOR_DIMENSION
from knowledge_base.schema import _text_view_body, ensure_vector_index


def test_text_view_indexes_body_once_via_chunks() -> None:
    # GR-6 / audit #14: the document body is indexed only through chunks, so a document is not
    # double-indexed as itself and as its chunks. Titles stay searchable on documents.
    links = _text_view_body()["links"]
    assert set(links["documents"]["fields"]) == {"title"}
    assert set(links["chunks"]["fields"]) == {"text"}


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
