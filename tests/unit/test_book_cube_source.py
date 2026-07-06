from pathlib import Path

import pytest

from knowledge_base.sources.book_cube import (
    DEFAULT_PUBLIC_URL,
    LiveFetchUnavailable,
    canonical_id_from_post,
    fetch_snapshot_payload,
    parse_snapshot,
    title_from_text,
    topic_key,
)


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


def test_parse_telegram_desktop_json_export() -> None:
    parsed = parse_snapshot(JSON_FIXTURE.read_text(encoding="utf-8"), media_type="application/json")

    assert parsed.title == "Книжный куб"
    assert len(parsed.items) == 2
    assert parsed.skipped == [{"guid": "202", "reason": "unsupported_type"}]
    assert parsed.items[0].canonical_id == "book_cube-201"
    assert parsed.items[0].url == "https://t.me/book_cube/201"
    assert parsed.items[0].tags == ["books", "notes"]
    assert parsed.items[1].tags == ["research"]


def test_title_and_canonical_helpers_are_stable() -> None:
    assert canonical_id_from_post("book_cube/101", None) == "book_cube-101"
    assert canonical_id_from_post(None, 201) == "book_cube-201"
    assert title_from_text("Очень длинная строка " * 10).endswith("...")


def test_topic_key_normalizes_hashtags() -> None:
    assert topic_key("#Books") == "books"
    assert topic_key("исследования").startswith("topic-topic-")


def test_live_fetch_unavailable_for_bad_url() -> None:
    with pytest.raises(LiveFetchUnavailable) as error:
        fetch_snapshot_payload("http://127.0.0.1:1/not-running", timeout_seconds=0.2)

    payload = error.value.to_payload(DEFAULT_PUBLIC_URL)
    assert payload["error"] == "live_fetch_unavailable"
    assert payload["source_key"] == "book-cube"
