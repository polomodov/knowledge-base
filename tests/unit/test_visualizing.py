from __future__ import annotations

import re
from typing import Any

import pytest

from knowledge_base.visualizing import (
    community_rollups,
    document_similarity_edges,
    document_similarity_projection,
    document_topic_memberships,
    ego_graph,
    timeline_buckets,
    topic_cooccurrence,
)


def _document(
    key: str,
    *,
    source: str = "source-1",
    published_at: str | None = "2026-01-05T00:00:00Z",
    status: str = "published",
) -> dict[str, Any]:
    return {
        "_key": key,
        "title": f"Document {key}",
        "source_key": source,
        "published_at": published_at,
        "url": f"https://example.test/{key}",
        "status": status,
    }


class _StubClient:
    def __init__(self) -> None:
        self.documents = [
            _document("d1"),
            _document("d2"),
            _document("d3", source="source-2", published_at="2026-02-07T00:00:00Z"),
            _document("d4", source="source-2", published_at=None),
            _document("draft", source="source-2", published_at="2026-02-08T00:00:00Z", status="draft"),
            _document("fixture", source="source-2", published_at="2026-02-09T00:00:00Z", status="fixture"),
        ]
        by_key = {document["_key"]: document for document in self.documents}
        topics = {
            "t1": {"_key": "t1", "label": "Topic One"},
            "t2": {"_key": "t2", "label": "Topic Two"},
            "t3": {"_key": "t3", "label": "Topic Three"},
        }
        self.topic_mentions = [
            {"document": by_key["d1"], "topic": topics["t1"]},
            {"document": by_key["d1"], "topic": topics["t1"]},  # document + chunk duplicate
            {"document": by_key["d1"], "topic": topics["t2"]},
            {"document": by_key["d2"], "topic": topics["t1"]},
            {"document": by_key["d2"], "topic": topics["t2"]},
            {"document": by_key["d3"], "topic": topics["t2"]},
            {"document": by_key["d3"], "topic": topics["t3"]},
            {"document": by_key["d4"], "topic": topics["t1"]},
            {"document": by_key["draft"], "topic": topics["t1"]},
            {"document": by_key["draft"], "topic": topics["t3"]},
            {"document": by_key["fixture"], "topic": topics["t1"]},
            {"document": None, "topic": topics["t1"]},
        ]
        self.similarity = [
            {"left": by_key["d1"], "right": by_key["d2"], "weight": 0.8},
            {"left": by_key["d2"], "right": by_key["d1"], "weight": 0.9004},
            {"left": by_key["d1"], "right": by_key["d3"], "weight": 0.9003},
            {"left": by_key["d2"], "right": by_key["d3"], "weight": 0.7},
            {"left": by_key["d2"], "right": by_key["d4"], "weight": 0.6},
            {"left": by_key["d1"], "right": by_key["draft"], "weight": 0.99},
        ]
        self.communities = [
            {
                "community": {
                    "_key": "comm-1",
                    "summary": "Stored summary",
                    "top_topics": ["Architecture", "SystemDesign", "Ignored third"],
                    "size": 99,
                },
                "documents": [by_key["d1"], by_key["d2"]],
            },
            {
                "community": {"_key": "comm-empty-label", "summary": "No topics", "top_topics": [], "size": 1},
                "documents": [by_key["d3"]],
            },
            {
                "community": {
                    "_key": "comm-with-draft",
                    "summary": "Contains SecretDraftTopic",
                    "top_topics": ["SecretDraftTopic"],
                    "size": 2,
                },
                "documents": [by_key["d4"], by_key["draft"]],
            },
        ]
        self.author_mentions = [
            {"document": by_key["d1"], "entity": {"_key": "a1", "display_name": "Shared Author"}},
            {"document": by_key["d3"], "entity": {"_key": "a1", "display_name": "Shared Author"}},
            {"document": by_key["d2"], "entity": {"_key": "a2", "display_name": "Neighbour only"}},
        ]
        self.work_mentions = [
            {"document": by_key["d1"], "entity": {"_key": "w1", "title": "Center only"}},
            {"document": by_key["draft"], "entity": {"_key": "w1", "title": "Center only"}},
        ]
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def aql(
        self,
        query: str,
        bind_vars: dict[str, Any] | None = None,
        *,
        batch_size: int | None = None,
    ) -> list[dict[str, Any]]:
        variables = bind_vars or {}
        self.calls.append((query, variables))
        if "viz:topic_mentions" in query:
            return self.topic_mentions
        if "viz:similarity" in query:
            return self.similarity
        if "viz:communities" in query:
            return self.communities
        if "viz:documents" in query:
            return self.documents
        if "viz:entity_mentions" in query:
            if variables["@edge_collection"] == "document_mentions_author":
                return self.author_mentions
            return self.work_mentions
        raise AssertionError(f"unexpected query: {query}")


class _StubRepository:
    def __init__(self) -> None:
        self.client = _StubClient()


def test_document_topic_memberships_deduplicate_document_and_chunk_mentions() -> None:
    repository = _StubRepository()

    rows = document_topic_memberships(repository)  # type: ignore[arg-type]

    assert [(row["document_key"], row["topic_key"]) for row in rows].count(("d1", "t1")) == 1
    assert len(rows) == 7
    assert all(row["document_key"] != "draft" for row in rows)
    with_drafts = document_topic_memberships(repository, include_drafts=True)  # type: ignore[arg-type]
    assert all(row["document_key"] != "fixture" for row in with_drafts)
    assert with_drafts[-2:] == [
        {
            "document_key": "draft",
            "topic_key": "t1",
            "topic_label": "Topic One",
            "source_key": "source-2",
            "published_at": "2026-02-08T00:00:00Z",
            "status": "draft",
        },
        {
            "document_key": "draft",
            "topic_key": "t3",
            "topic_label": "Topic Three",
            "source_key": "source-2",
            "published_at": "2026-02-08T00:00:00Z",
            "status": "draft",
        },
    ]


def test_document_similarity_fold_uses_max_chunk_pairs_and_top_k_union() -> None:
    repository = _StubRepository()

    full = document_similarity_edges(repository, top_k=None)  # type: ignore[arg-type]

    assert full == [
        {"source": "d1", "target": "d2", "weight": 0.9, "chunk_pairs": 2},
        {"source": "d1", "target": "d3", "weight": 0.9, "chunk_pairs": 1},
        {"source": "d2", "target": "d3", "weight": 0.7, "chunk_pairs": 1},
        {"source": "d2", "target": "d4", "weight": 0.6, "chunk_pairs": 1},
    ]
    # d1 chooses d2 using the unrounded 0.9004 > 0.9003 weights; d3 chooses
    # d1 and d4 chooses d2, so their per-document choices form this union.
    assert document_similarity_edges(repository, top_k=1) == [  # type: ignore[arg-type]
        {"source": "d1", "target": "d2", "weight": 0.9, "chunk_pairs": 2},
        {"source": "d1", "target": "d3", "weight": 0.9, "chunk_pairs": 1},
        {"source": "d2", "target": "d4", "weight": 0.6, "chunk_pairs": 1},
    ]
    projection = document_similarity_projection(repository, top_k=1)  # type: ignore[arg-type]
    assert projection["neighbors"] == {"d1": ["d2"], "d2": ["d1"], "d3": ["d1"], "d4": ["d2"]}
    assert any(  # type: ignore[arg-type]
        edge["target"] == "draft" for edge in document_similarity_edges(repository, top_k=None, include_drafts=True)
    )


def test_topic_cooccurrence_counts_distinct_documents_and_applies_threshold() -> None:
    repository = _StubRepository()

    assert topic_cooccurrence(repository, min_documents=2) == [  # type: ignore[arg-type]
        {
            "source": "t1",
            "target": "t2",
            "source_label": "Topic One",
            "target_label": "Topic Two",
            "document_count": 2,
        }
    ]
    all_pairs = topic_cooccurrence(repository, min_documents=1)  # type: ignore[arg-type]
    assert [(row["source"], row["target"], row["document_count"]) for row in all_pairs] == [
        ("t1", "t2", 2),
        ("t2", "t3", 1),
    ]


def test_community_rollups_use_stored_description_and_live_membership() -> None:
    repository = _StubRepository()

    rows = community_rollups(repository)  # type: ignore[arg-type]

    assert rows[0] == {
        "community_key": "comm-1",
        "label": "Architecture · SystemDesign",
        "size": 2,
        "summary": "Stored summary",
        "top_topics": ["Architecture", "SystemDesign", "Ignored third"],
        "documents": ["d1", "d2"],
    }
    assert rows[1]["label"] == "comm-empty-label"
    default_keys = {row["community_key"] for row in rows}
    assert "comm-with-draft" not in default_keys
    with_drafts = community_rollups(repository, include_drafts=True)  # type: ignore[arg-type]
    draft_community = next(row for row in with_drafts if row["community_key"] == "comm-with-draft")
    assert draft_community["top_topics"] == ["SecretDraftTopic"]
    assert draft_community["size"] == 2


def test_timeline_has_continuous_months_sources_topics_and_missing_date_count() -> None:
    repository = _StubRepository()

    timeline = timeline_buckets(repository, top_topics=2)  # type: ignore[arg-type]

    assert timeline == {
        "months": ["2026-01", "2026-02"],
        "by_source": [
            {"month": "2026-01", "source_key": "source-1", "documents": 2},
            {"month": "2026-02", "source_key": "source-2", "documents": 1},
        ],
        "topics": [
            {"topic_key": "t1", "label": "Topic One", "documents": 3},
            {"topic_key": "t2", "label": "Topic Two", "documents": 3},
        ],
        "by_topic": [
            {"month": "2026-01", "topic_key": "t1", "documents": 2},
            {"month": "2026-01", "topic_key": "t2", "documents": 2},
            {"month": "2026-02", "topic_key": "t2", "documents": 1},
        ],
        "docs_without_dates": 1,
    }


def test_ego_graph_contains_induced_neighbours_and_center_shared_entities() -> None:
    repository = _StubRepository()

    graph = ego_graph(repository, "d1", neighbor_limit=2)  # type: ignore[arg-type]

    assert graph["center"]["document_key"] == "d1"
    assert [document["document_key"] for document in graph["documents"]] == ["d1", "d2", "d3"]
    assert [(edge["source"], edge["target"]) for edge in graph["similarity_edges"]] == [
        ("d1", "d2"),
        ("d1", "d3"),
        ("d2", "d3"),
    ]
    assert [(entity["id"], entity["document_keys"]) for entity in graph["entities"]] == [
        ("author:a1", ["d1", "d3"]),
        ("topic:t1", ["d1", "d2"]),
        ("topic:t2", ["d1", "d2", "d3"]),
    ]
    assert {edge["entity_id"] for edge in graph["entity_edges"]} == {"author:a1", "topic:t1", "topic:t2"}


def test_ego_graph_rejects_missing_or_filtered_center() -> None:
    repository = _StubRepository()

    with pytest.raises(ValueError, match="unknown or unpublished document: missing"):
        ego_graph(repository, "missing")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unknown or unpublished document: draft"):
        ego_graph(repository, "draft")  # type: ignore[arg-type]


def test_aggregations_issue_read_only_aql() -> None:
    repository = _StubRepository()
    document_topic_memberships(repository)  # type: ignore[arg-type]
    document_similarity_edges(repository)  # type: ignore[arg-type]
    topic_cooccurrence(repository)  # type: ignore[arg-type]
    community_rollups(repository)  # type: ignore[arg-type]
    timeline_buckets(repository)  # type: ignore[arg-type]
    ego_graph(repository, "d1")  # type: ignore[arg-type]

    write_token = re.compile(r"\b(?:INSERT|UPDATE|REPLACE|REMOVE|UPSERT)\b", re.IGNORECASE)
    assert repository.client.calls
    assert all(write_token.search(query) is None for query, _ in repository.client.calls)


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        (lambda repository: document_similarity_edges(repository, top_k=0), "top_k"),
        (lambda repository: topic_cooccurrence(repository, min_documents=0), "min_documents"),
        (lambda repository: timeline_buckets(repository, top_topics=-1), "top_topics"),
        (lambda repository: ego_graph(repository, "d1", neighbor_limit=-1), "neighbor_limit"),
    ],
)
def test_public_limits_are_validated(operation, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        operation(_StubRepository())
