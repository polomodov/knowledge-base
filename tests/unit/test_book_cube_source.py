from pathlib import Path

import pytest

from knowledge_base.ids import work_key
from knowledge_base.sources.book_cube import (
    DEFAULT_PUBLIC_URL,
    LiveFetchUnavailable,
    canonical_id_from_post,
    extract_works,
    fetch_snapshot_payload,
    parse_snapshot,
    title_from_text,
    topic_key,
)
from knowledge_base.sources.contracts import NormalizedSourceItem
from knowledge_base.sources.ingest_core import empty_counts, upsert_works

HTML_FIXTURE = Path("tests/fixtures/book_cube_channel.html")
JSON_FIXTURE = Path("tests/fixtures/book_cube_export.json")


def test_parse_telegram_public_html_snapshot() -> None:
    parsed = parse_snapshot(HTML_FIXTURE.read_text(encoding="utf-8"), media_type="text/html")

    assert parsed.title == "Книжный куб"
    assert len(parsed.items) == 2
    assert parsed.skipped == [{"guid": "book_cube/103", "reason": "empty_text"}]

    first = parsed.items[0]
    assert first.canonical_id == "book_cube-101"
    assert first.url == "https://t.me/book_cube/101"
    assert first.published_at == "2026-06-20T09:15:00Z"
    assert first.title == "Книжная заметка про системное чтение."
    assert first.tags == ["books", "systems"]
    assert "связывать цитаты" in first.text
    assert len(first.works) == 1
    assert first.works[0].title == "Thinking in Systems"
    assert first.works[0].key == "thinking-in-systems"
    assert parsed.items[1].works == []


def test_parse_telegram_desktop_json_export() -> None:
    parsed = parse_snapshot(JSON_FIXTURE.read_text(encoding="utf-8"), media_type="application/json")

    assert parsed.title == "Книжный куб"
    assert len(parsed.items) == 2
    assert parsed.skipped == [{"guid": "202", "reason": "unsupported_type"}]
    assert parsed.items[0].canonical_id == "book_cube-201"
    assert parsed.items[0].url == "https://t.me/book_cube/201"
    assert parsed.items[0].tags == ["books", "notes"]
    assert len(parsed.items[0].works) == 1
    assert parsed.items[0].works[0].title == "Knowledge Graphs for Notes"
    assert parsed.items[0].works[0].key == "knowledge-graphs-for-notes"
    assert parsed.items[1].tags == ["research"]
    assert parsed.items[1].works == []


def test_title_and_canonical_helpers_are_stable() -> None:
    assert canonical_id_from_post("book_cube/101", None) == "book_cube-101"
    assert canonical_id_from_post(None, 201) == "book_cube-201"
    assert title_from_text("Очень длинная строка " * 10).endswith("...")


def test_topic_key_normalizes_hashtags() -> None:
    assert topic_key("#Books") == "books"
    key = topic_key("исследования")
    assert key.startswith("topic-")
    assert not key.startswith("topic-topic-")


def test_live_fetch_unavailable_for_bad_url() -> None:
    with pytest.raises(LiveFetchUnavailable) as error:
        fetch_snapshot_payload("http://127.0.0.1:1/not-running", timeout_seconds=0.2)

    payload = error.value.to_payload(DEFAULT_PUBLIC_URL)
    assert payload["error"] == "live_fetch_unavailable"
    assert payload["source_key"] == "book-cube"


def test_parse_snapshot_html_without_messages_is_empty() -> None:
    # An HTML page with no Telegram message widgets yields no items (finding #46).
    parsed = parse_snapshot("<html><body><div>no telegram messages here</div></body></html>", media_type="text/html")
    assert parsed.items == []


def test_parse_snapshot_json_without_messages_key_is_empty() -> None:
    parsed = parse_snapshot('{"name": "Книжный куб"}', media_type="application/json")
    assert parsed.items == []
    assert parsed.title == "Книжный куб"


def test_parse_snapshot_html_void_tags_do_not_drop_messages() -> None:
    # A message containing void tags (<img> photo, <br>) must not unbalance the depth counter
    # and drop itself or the following message (finding #22).
    html = (
        '<div class="tgme_widget_message" data-post="book_cube/1">'
        '<img class="tgme_widget_message_photo" src="x.jpg">'
        '<div class="tgme_widget_message_text">First message.<br>with a break</div></div>'
        '<div class="tgme_widget_message" data-post="book_cube/2">'
        '<div class="tgme_widget_message_text">Second message here.</div></div>'
    )
    parsed = parse_snapshot(html, media_type="text/html")
    assert sorted(item.metadata["message_id"] for item in parsed.items) == ["1", "2"]
    assert parsed.items[0].text == "First message. with a break"


def test_extract_works_from_clear_title_pattern() -> None:
    works = extract_works("Сегодня разбираю «Системное мышление» и заметки вокруг неё.")
    assert len(works) == 1
    assert works[0].title == "Системное мышление"
    assert works[0].key == work_key("Системное мышление")
    assert works[0].key.startswith("work-")
    assert "«Системное мышление»" in works[0].evidence


def test_extract_works_ignores_urls_and_short_noise() -> None:
    assert extract_works('Ссылка "https://example.com/book" не книга.') == []
    assert extract_works("Пустые «» кавычки.") == []
    assert extract_works("Маркер book: x") == []  # too short / filtered


def test_quoted_work_upsert_creates_work_and_edge() -> None:
    class _FakeRepository:
        def __init__(self) -> None:
            self.docs: dict[str, list[dict]] = {}

        def upsert(self, collection: str, document: dict) -> dict:
            self.docs.setdefault(collection, []).append(document)
            return {"created": True, "document": document}

        def upsert_edge(self, collection: str, edge: dict) -> dict:
            return self.upsert(collection, edge)

    repository = _FakeRepository()
    works = extract_works('Читаю "Thinking in Systems" перед конспектом.')
    item = NormalizedSourceItem(
        canonical_id="book_cube-999",
        title="test",
        text='Читаю "Thinking in Systems" перед конспектом.',
        url="https://t.me/book_cube/999",
        guid="999",
        published_at=None,
        language="unknown",
        author=None,
        tags=[],
        works=works,
    )
    counts = empty_counts()
    upsert_works(
        repository,  # type: ignore[arg-type]
        item,
        "doc-test",
        "book-cube",
        "import-1",
        "2026-07-13T00:00:00Z",
        counts,
        method="telegram_title_pattern",
        provenance={"source_key": "book-cube"},
    )

    assert counts["works"] == 1
    assert counts["edges"] == 1
    assert repository.docs["works"][0]["_key"] == "thinking-in-systems"
    assert repository.docs["works"][0]["title"] == "Thinking in Systems"
    edge = repository.docs["document_references_work"][0]
    assert edge["_from"] == "documents/doc-test"
    assert edge["_to"] == "works/thinking-in-systems"
    assert edge["method"] == "telegram_title_pattern"
