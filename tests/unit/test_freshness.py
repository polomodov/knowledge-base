from knowledge_base.freshness import (
    COMMUNITIES_MISSING_AFTER_RELATED,
    COMMUNITIES_OLDER_THAN_RELATED,
    COMMUNITIES_STALE_AFTER_EMBEDDINGS,
    COMMUNITIES_STALE_AFTER_IMPORT,
    RELATED_MISSING_AFTER_EMBEDDINGS,
    RELATED_OLDER_THAN_EMBEDDINGS,
    RELATED_STALE_AFTER_IMPORT,
    derived_index_stale_codes,
    derived_index_stale_messages,
)


def test_derived_index_stale_codes_detect_missing_and_older_layers() -> None:
    assert derived_index_stale_codes({}) == ()
    assert derived_index_stale_codes({"embeddings": {"finished_at": "2026-07-10T00:00:00Z"}}) == (
        RELATED_MISSING_AFTER_EMBEDDINGS,
    )
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
    assert (
        derived_index_stale_codes(
            {
                "embeddings": {"finished_at": "2026-07-10T00:00:00Z"},
                "related": {"finished_at": "2026-07-11T00:00:00Z"},
                "communities": {"finished_at": "2026-07-12T00:00:00Z"},
            }
        )
        == ()
    )


def test_derived_index_stale_codes_warn_communities_when_embeddings_invalidate_related() -> None:
    # Rebuilt embeddings clear related edges; communities can still look newer than stale related.
    assert derived_index_stale_codes(
        {
            "embeddings": {"finished_at": "2026-07-12T00:00:00Z"},
            "related": {"finished_at": "2026-07-10T00:00:00Z"},
            "communities": {"finished_at": "2026-07-11T00:00:00Z"},
        }
    ) == (RELATED_OLDER_THAN_EMBEDDINGS, COMMUNITIES_STALE_AFTER_EMBEDDINGS)
    assert derived_index_stale_codes(
        {
            "embeddings": {"finished_at": "2026-07-12T00:00:00Z"},
            "communities": {"finished_at": "2026-07-11T00:00:00Z"},
        }
    ) == (RELATED_MISSING_AFTER_EMBEDDINGS, COMMUNITIES_STALE_AFTER_EMBEDDINGS)


def test_derived_index_stale_codes_anchor_related_and_communities_to_a_later_import() -> None:
    # An incremental ingest writes chunk embeddings inline but does not rebuild the similarity graph
    # or communities, so both must be flagged stale when the latest import is newer than them.
    runs = {
        "embeddings": {"finished_at": "2026-07-10T00:00:00Z"},
        "related": {"finished_at": "2026-07-10T00:00:00Z"},
        "communities": {"finished_at": "2026-07-10T00:00:00Z"},
    }
    assert derived_index_stale_codes(runs, import_finished_at="2026-07-12T00:00:00Z") == (
        RELATED_STALE_AFTER_IMPORT,
        COMMUNITIES_STALE_AFTER_IMPORT,
    )
    # An import that predates the derived indexes raises nothing.
    assert derived_index_stale_codes(runs, import_finished_at="2026-07-01T00:00:00Z") == ()
    # The import anchor never overrides the stronger embeddings-vs-related signal.
    assert derived_index_stale_codes(
        {
            "embeddings": {"finished_at": "2026-07-12T00:00:00Z"},
            "related": {"finished_at": "2026-07-11T00:00:00Z"},
            "communities": {"finished_at": "2026-07-11T00:00:00Z"},
        },
        import_finished_at="2026-07-13T00:00:00Z",
    ) == (RELATED_OLDER_THAN_EMBEDDINGS, COMMUNITIES_STALE_AFTER_EMBEDDINGS)


def test_derived_index_stale_codes_compare_mixed_precision_timestamps() -> None:
    # Index runs carry microseconds while import runs may be whole-second; datetime comparison keeps
    # ordering correct where a raw string compare (\".123456Z\" < \"Z\") would not.
    runs = {
        "embeddings": {"finished_at": "2026-07-10T00:00:00.500000Z"},
        "related": {"finished_at": "2026-07-10T00:00:00.400000Z"},
    }
    assert RELATED_OLDER_THAN_EMBEDDINGS in derived_index_stale_codes(runs)


def test_derived_index_stale_messages_include_codes() -> None:
    messages = derived_index_stale_messages({"embeddings": {"finished_at": "2026-07-10T00:00:00Z"}})
    assert messages == [
        {
            "code": RELATED_MISSING_AFTER_EMBEDDINGS,
            "message": messages[0]["message"],
        }
    ]
    assert "related" in messages[0]["message"]
