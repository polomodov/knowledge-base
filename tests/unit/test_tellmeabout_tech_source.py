from pathlib import Path

import pytest

from knowledge_base.ids import topic_key
from knowledge_base.sources.tellmeabout_tech import (
    DEFAULT_FEED_URL,
    LiveFetchUnavailable,
    canonical_id_from_url_or_guid,
    fetch_feed_payload,
    html_to_text,
    parse_feed,
)

FIXTURE = Path("tests/fixtures/tellmeabout_tech_feed.xml")


def test_parse_medium_like_rss_feed() -> None:
    payload = FIXTURE.read_text(encoding="utf-8")
    parsed = parse_feed(payload)

    assert parsed.title == "Tell Me About Tech"
    assert len(parsed.items) == 2
    assert parsed.skipped == [{"guid": "empty-draft", "reason": "empty_text"}]

    first = parsed.items[0]
    assert first.title == "Systems For Better Technical Writing"
    assert first.url == "https://tellmeabout.tech/systems-for-better-technical-writing"
    assert first.canonical_id == "systems-for-better-technical-writing"
    assert first.published_at == "2026-06-23T10:00:00Z"
    assert first.author == "Tell Me About Tech Author"
    assert first.tags == ["Product Thinking", "Writing Systems"]
    assert "Technical writing improves" in first.text
    assert "durable knowledge base" in first.text


def test_html_to_text_is_stable() -> None:
    html = "<h1>Title</h1><p>Hello&nbsp;<strong>world</strong>.</p><p>Next line.</p>"
    assert html_to_text(html) == "Title Hello world. Next line."


def test_canonical_id_prefers_url_path_and_falls_back_to_guid() -> None:
    assert canonical_id_from_url_or_guid("https://tellmeabout.tech/post-one?sk=abc", "ignored") == "post-one"
    assert canonical_id_from_url_or_guid("", "medium-fixture-post-002") == "medium-fixture-post-002"


def test_topic_key_normalizes_tags() -> None:
    assert topic_key("Product Thinking") == "product-thinking"
    assert topic_key("AI Tools") == "ai-tools"
    # Non-ASCII tags must not all collapse into a single "topic" bucket (finding #1).
    assert topic_key("машинное обучение") != topic_key("базы данных")
    assert topic_key("машинное обучение") != "topic"


def test_live_fetch_unavailable_for_bad_url() -> None:
    with pytest.raises(LiveFetchUnavailable) as error:
        fetch_feed_payload("http://127.0.0.1:1/not-running", timeout_seconds=0.2)

    assert error.value.feed_url == "http://127.0.0.1:1/not-running"
    assert error.value.to_payload(DEFAULT_FEED_URL)["error"] == "live_fetch_unavailable"


def test_parse_rss_item_missing_link_and_title_falls_back() -> None:
    # An item without <link>/<title> must still parse with a stable canonical id from the
    # guid and a default title, not produce null provenance (finding #46).
    rss = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        "<title>Feed</title><link>https://tellmeabout.tech/</link>"
        "<item><guid>post-42</guid><description>Some body text here.</description></item>"
        "</channel></rss>"
    )
    parsed = parse_feed(rss)
    assert len(parsed.items) == 1
    item = parsed.items[0]
    assert item.title == "Untitled"
    assert item.canonical_id == "post-42"  # derived from guid since there is no link
    assert item.url is None


def test_parse_rss_skips_item_with_empty_text() -> None:
    rss = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        "<title>Feed</title><link>https://tellmeabout.tech/</link>"
        "<item><title>Empty</title><link>https://tellmeabout.tech/empty</link><description></description></item>"
        "</channel></rss>"
    )
    parsed = parse_feed(rss)
    assert parsed.items == []
    assert parsed.skipped and parsed.skipped[0]["reason"] == "empty_text"
