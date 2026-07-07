from knowledge_base.sources.ingest_core import parse_date


def test_parse_date_normalizes_iso_and_rfc2822() -> None:
    assert parse_date("2026-06-20T09:15:00Z") == "2026-06-20T09:15:00Z"
    assert parse_date("2026-06-20T12:15:00+03:00") == "2026-06-20T09:15:00Z"  # converted to UTC
    assert parse_date("Sat, 20 Jun 2026 09:15:00 +0000") == "2026-06-20T09:15:00Z"


def test_parse_date_returns_none_for_missing_or_unparseable() -> None:
    # published_at must be a normalized ISO string or null, never arbitrary junk (finding #25).
    assert parse_date(None) is None
    assert parse_date("") is None
    assert parse_date("not a date") is None
    assert parse_date("Foo 99, 2024") is None
