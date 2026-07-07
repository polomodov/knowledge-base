from knowledge_base.ids import chunk_key, document_key, sha256_text, slugify, stable_key, topic_key
from knowledge_base.sources import book_cube


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


def test_topic_key_ascii_is_readable_slug() -> None:
    assert topic_key("#Books") == "books"
    assert topic_key("Product Thinking") == "product-thinking"
    assert topic_key("AI Tools") == "ai-tools"


def test_topic_key_non_ascii_is_stable_and_collision_free() -> None:
    ml = topic_key("машинное обучение")
    db = topic_key("базы данных")
    assert ml.startswith("topic-")
    assert not ml.startswith("topic-topic-")
    assert ml != db  # distinct Cyrillic labels -> distinct keys (finding #1)
    assert ml != "topic"  # never collapses into the shared fallback bucket
    assert topic_key("машинное обучение") == ml  # deterministic


def test_topic_key_is_shared_across_adapters() -> None:
    # A single canonical implementation in ids is used everywhere via ingest_core (finding #2);
    # adapters no longer define their own. book_cube re-exports the canonical one for hashtags.
    assert book_cube.topic_key is topic_key
    for label in ("Books", "машинное обучение", "AI Tools"):
        assert book_cube.topic_key(label) == topic_key(label)


def test_topic_key_empty_label_falls_back() -> None:
    assert topic_key("#") == "topic"
    assert topic_key("   ") == "topic"
