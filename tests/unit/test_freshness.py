from knowledge_base.freshness import (
    COMMUNITIES_MISSING_AFTER_RELATED,
    COMMUNITIES_OLDER_THAN_RELATED,
    RELATED_MISSING_AFTER_EMBEDDINGS,
    RELATED_OLDER_THAN_EMBEDDINGS,
    derived_index_stale_codes,
    derived_index_stale_messages,
)


def test_derived_index_stale_codes_detect_missing_and_older_layers() -> None:
    assert derived_index_stale_codes({}) == ()
    assert derived_index_stale_codes(
        {"embeddings": {"finished_at": "2026-07-10T00:00:00Z"}}
    ) == (RELATED_MISSING_AFTER_EMBEDDINGS,)
    assert derived_index_stale_codes(
        {
            "embeddings": {"finished_at": "2026-07-12T00:00:00Z"},
            "related": {"finished_at": "2026-07-11T00:00:00Z"},
        }
    ) == (RELATED_OLDER_THAN_EMBEDDINGS, COMMUNITIES_MISSING_AFTER_RELATED)
    assert derived_index_stale_codes(
        {
            "related": {"finished_at": "2026-07-12T00:00:00Z"},
            "communities": {"finished_at": "2026-07-11T00:00:00Z"},
        }
    ) == (COMMUNITIES_OLDER_THAN_RELATED,)
    assert derived_index_stale_codes(
        {
            "embeddings": {"finished_at": "2026-07-10T00:00:00Z"},
            "related": {"finished_at": "2026-07-11T00:00:00Z"},
            "communities": {"finished_at": "2026-07-12T00:00:00Z"},
        }
    ) == ()


def test_derived_index_stale_messages_include_codes() -> None:
    messages = derived_index_stale_messages({"embeddings": {"finished_at": "2026-07-10T00:00:00Z"}})
    assert messages == [
        {
            "code": RELATED_MISSING_AFTER_EMBEDDINGS,
            "message": messages[0]["message"],
        }
    ]
    assert "related" in messages[0]["message"]
