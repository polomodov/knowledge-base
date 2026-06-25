from knowledge_base.ids import chunk_key, document_key, sha256_text, slugify, stable_key


def test_stable_key_is_deterministic() -> None:
    assert stable_key("Source", "Document", prefix="doc") == stable_key("Source", "Document", prefix="doc")


def test_slugify_keeps_arangodb_safe_key_shape() -> None:
    assert slugify("Systems Thinking / Notes!") == "systems-thinking-notes"


def test_document_and_chunk_keys_are_stable() -> None:
    doc_key = document_key("fixture", "systems-thinking")
    assert doc_key == document_key("fixture", "systems-thinking")
    assert chunk_key(doc_key, 0, "hello") == chunk_key(doc_key, 0, "hello")
    assert chunk_key(doc_key, 0, "hello") != chunk_key(doc_key, 1, "hello")


def test_sha256_text() -> None:
    assert sha256_text("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
