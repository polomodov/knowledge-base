from typing import cast

import pytest

from knowledge_base.repository import KnowledgeRepository
from knowledge_base.retrieval import (
    _cosine,
    _dedup_best_by_document,
    _gate_by_similarity,
    _graph_boosts,
    _merge_hybrid,
    _start_vertex,
    _vector_ranked,
    graph_neighbors,
    semantic_search,
    text_search,
)


def test_cosine_similarity_edge_cases() -> None:
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == 0.0  # orthogonal
    assert _cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 1.0  # identical
    assert _cosine([1.0, 1.0], [-1.0, -1.0]) == -1.0  # opposite
    assert _cosine([0.0, 0.0], [1.0, 2.0]) == 0.0  # zero vector -> 0, no ZeroDivisionError


def test_start_vertex_precedence_and_none() -> None:
    assert _start_vertex(topic="t", author="a", work=None, document=None, chunk=None) == "topics/t"
    assert _start_vertex(topic=None, author="a", work="w", document=None, chunk=None) == "authors/a"
    assert _start_vertex(topic=None, author=None, work="w", document="d", chunk=None) == "works/w"
    assert _start_vertex(topic=None, author=None, work=None, document="d", chunk="c") == "documents/d"
    assert _start_vertex(topic=None, author=None, work=None, document=None, chunk="c") == "chunks/c"
    assert _start_vertex(topic=None, author=None, work=None, document=None, chunk=None) is None


def _text(document_key: str, bm25: float, *, chunk_key: str | None = None, snippet: str = "snippet") -> dict:
    return {
        "id": f"documents/{document_key}",
        "document_key": document_key,
        "chunk_key": chunk_key,
        "title": document_key,
        "snippet": snippet,
        "score": bm25,
        "score_components": {"bm25": bm25, "vector": None, "graph_boost": None},
        "provenance": {"source_key": "s", "raw_snapshot_key": "r", "import_run_key": "i"},
    }


def _semantic(document_key: str, cosine: float, *, chunk_key: str = "c0") -> dict:
    return {
        "id": f"chunks/{chunk_key}",
        "document_key": document_key,
        "chunk_key": chunk_key,
        "title": document_key,
        "snippet": "snippet",
        "score": cosine,
        "score_components": {"bm25": None, "vector": cosine, "graph_boost": None},
        "provenance": {"source_key": "s", "raw_snapshot_key": "r", "import_run_key": "i"},
    }


def test_merge_hybrid_negative_cosine_does_not_subtract() -> None:
    # A semantic-only hit with a negative cosine must not score below zero (finding #16).
    results = _merge_hybrid([], [_semantic("d1", -0.4)], limit=10)
    assert len(results) == 1
    assert results[0]["score"] == 0.0
    assert results[0]["score_components"]["vector"] == -0.4  # raw value preserved for transparency


def test_merge_hybrid_collapses_document_and_its_chunks() -> None:
    # One document surfacing as a document-row and a chunk-row must not take two slots (finding #14).
    text = [_text("d1", 5.0, chunk_key=None), _text("d1", 3.0, chunk_key="d1-c0")]
    results = _merge_hybrid(text, [], limit=10)
    assert len(results) == 1
    assert results[0]["document_key"] == "d1"
    assert results[0]["score_components"]["bm25"] == 5.0  # keeps the higher-BM25 representative


def test_merge_hybrid_ranks_text_and_combines_vector() -> None:
    text = [_text("d1", 10.0), _text("d2", 2.0)]
    semantic = [_semantic("d2", 0.9)]
    results = _merge_hybrid(text, semantic, limit=10)
    scores = {result["document_key"]: result["score"] for result in results}
    assert scores["d1"] == 1.0  # top BM25 -> 1.0, no vector
    assert round(scores["d2"], 6) == 0.9  # bottom BM25 -> 0.0, plus clamped cosine 0.9
    assert results[0]["document_key"] == "d1"


def test_merge_hybrid_graph_boost_is_null_not_lexical() -> None:
    # Hybrid must not fabricate a graph_boost from snippet substring matching (finding #15).
    results = _merge_hybrid([_text("d1", 5.0, snippet="machine learning")], [], limit=10)
    assert results[0]["score_components"]["graph_boost"] is None


def test_merge_hybrid_respects_limit() -> None:
    text = [_text(f"d{i}", float(i)) for i in range(5)]
    results = _merge_hybrid(text, [], limit=2)
    assert len(results) == 2
    assert [result["document_key"] for result in results] == ["d4", "d3"]


def _fused(document_key: str, score: float) -> dict:
    return {
        "document_key": document_key,
        "score": score,
        "score_components": {"bm25": None, "vector": None, "graph_boost": None},
    }


def test_graph_boosts_reward_sharing_entities_with_strong_seeds() -> None:
    # d2 shares a topic with the strongest seed d1 and is boosted; d3 shares nothing (GR-1).
    fused = [_fused("d1", 1.0), _fused("d2", 0.4), _fused("d3", 0.4)]
    entity_sets = {"d1": {"topics/t1"}, "d2": {"topics/t1"}, "d3": {"topics/t9"}}
    boosts = _graph_boosts(fused, entity_sets, seed_count=5, cap=0.5)
    assert boosts["d2"] == pytest.approx(0.5)  # shares with the strongest seed -> top raw boost -> hits the cap
    assert boosts["d1"] == pytest.approx(0.2)  # only reinforced by the weaker seed d2
    assert boosts["d3"] == pytest.approx(0.0)  # shares no entity with any other seed
    assert all(0.0 <= value <= 0.5 for value in boosts.values())


def test_graph_boosts_reward_similarity_links() -> None:
    # A document with no shared entity but a strong item_related_to_item link to the top seed is
    # boosted just like a shared-entity neighbour (GR-3b).
    fused = [_fused("d1", 1.0), _fused("d2", 0.4), _fused("d3", 0.4)]
    related = {"d1": {"d2": 0.8}, "d2": {"d1": 0.8}}  # d3 is linked to nobody
    boosts = _graph_boosts(fused, {}, related=related, seed_count=5, cap=0.5)
    assert boosts["d2"] == pytest.approx(0.5)  # linked to the strongest seed -> top raw -> cap
    assert boosts["d1"] == pytest.approx(0.2)  # linked only to the weaker seed d2
    assert boosts["d3"] == pytest.approx(0.0)  # no graph connection to any seed


def test_graph_boosts_exclude_self_and_missing_entities() -> None:
    # A single candidate has no other seed to share with -> no self-boost.
    assert _graph_boosts([_fused("d1", 1.0)], {"d1": {"topics/t1"}}) == {"d1": 0.0}
    # No entities anywhere -> every boost is zero, never negative.
    assert _graph_boosts([_fused("d1", 1.0), _fused("d2", 0.5)], {}) == {"d1": 0.0, "d2": 0.0}
    # Empty candidate set -> empty mapping.
    assert _graph_boosts([], {}) == {}


class _FakeClient:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.requested: list[int] = []

    def aql(self, query: str, bind_vars: dict) -> list[dict]:
        candidates = bind_vars["candidates"]
        self.requested.append(candidates)
        return [dict(row) for row in self.rows[:candidates]]


class _FakeRepository:
    def __init__(self, rows: list[dict]) -> None:
        self.client = _FakeClient(rows)


def test_vector_ranked_grows_window_until_limit_is_filled() -> None:
    # One long document owns the 200 nearest chunks; 19 other documents sit just below.
    # A fixed candidate cap would return only that one document, so the window must grow
    # until `limit` distinct documents are collected (PR #8 review).
    rows = [
        {
            "id": f"d1-c{i}",
            "key": f"d1-c{i}",
            "document_key": "d1",
            "text": "t",
            "score": 1.0 - i * 1e-4,
            "embedding_model": "hash-v1",
        }
        for i in range(200)
    ] + [
        {
            "id": f"d{j}-c0",
            "key": f"d{j}-c0",
            "document_key": f"d{j}",
            "text": "t",
            "score": 0.5 - j * 1e-4,
            "embedding_model": "hash-v1",
        }
        for j in range(2, 21)
    ]
    repository = _FakeRepository(rows)
    ranked = _vector_ranked(cast(KnowledgeRepository, repository), [0.0] * 8, limit=10, source_key=None, model="hash-v1")

    assert ranked is not None
    document_keys = {item["document_key"] for item in ranked}
    assert len(document_keys) >= 10  # not starved to a single document
    assert {"d1", "d20"} <= document_keys
    assert repository.client.requested[0] == 100  # started at max(limit*10, 50)
    assert len(repository.client.requested) >= 2  # had to grow at least once


def test_vector_ranked_skips_index_for_source_filter() -> None:
    # The ANN index cannot be combined with a source filter, so that case falls back without
    # querying the index. Non-default dimensions are served by the index itself (finding #33).
    repository = _FakeRepository([])
    assert _vector_ranked(cast(KnowledgeRepository, repository), [0.0] * 8, limit=10, source_key="src", model="hash-v1") is None
    assert repository.client.requested == []  # never queried the index for a source-filtered request


def test_vector_ranked_filters_out_incompatible_embedding_models() -> None:
    # The vector index mixes every same-dimension model; a chunk from another provider/model must
    # not leak into results even though it is dimensionally scored by APPROX_NEAR_COSINE (GR-2 review).
    rows = [
        {"id": "a-c0", "key": "a-c0", "document_key": "a", "text": "t", "score": 0.9, "embedding_model": "model-x"},
        {"id": "b-c0", "key": "b-c0", "document_key": "b", "text": "t", "score": 0.8, "embedding_model": "model-y"},
        {"id": "c-c0", "key": "c-c0", "document_key": "c", "text": "t", "score": 0.7, "embedding_model": "model-x"},
    ]
    repository = _FakeRepository(rows)
    ranked = _vector_ranked(cast(KnowledgeRepository, repository), [0.0] * 8, limit=10, source_key=None, model="model-x")

    assert ranked is not None
    assert {item["document_key"] for item in ranked} == {"a", "c"}  # the model-y chunk is excluded


def test_gate_by_similarity_drops_below_floor() -> None:
    ranked = [{"score": 0.9}, {"score": 0.1}, {"score": 0.0}, {"score": -0.2}]
    # Default floor 0.0 removes only anti-correlated (negative) hits.
    assert _gate_by_similarity(ranked, 0.0) == [{"score": 0.9}, {"score": 0.1}, {"score": 0.0}]
    # A higher floor keeps only clearly-relevant hits.
    assert _gate_by_similarity(ranked, 0.5) == [{"score": 0.9}]
    # A floor of -1.0 keeps everything (cosine is always >= -1).
    assert _gate_by_similarity(ranked, -1.0) == ranked


def test_dedup_best_by_document_keeps_first_per_document() -> None:
    # Input is sorted by score descending; the first (best) chunk per document wins,
    # and order is preserved (finding #14).
    scored = [
        {"id": "c1", "document_key": "d1", "score": 0.9},
        {"id": "c2", "document_key": "d1", "score": 0.7},
        {"id": "c3", "document_key": "d2", "score": 0.6},
    ]
    deduped = _dedup_best_by_document(scored)
    assert [item["document_key"] for item in deduped] == ["d1", "d2"]
    assert deduped[0]["id"] == "c1"  # best chunk of d1, not the 0.7 one


def _legacy_result(document_key: str, visibility: str, score: float) -> dict:
    return {
        "id": f"chunks/{document_key}-c0",
        "document_key": document_key,
        "chunk_key": f"{document_key}-c0",
        "title": f"{visibility} document",
        "snippet": f"legacy {visibility} result",
        "score": score,
        "score_components": {"bm25": score, "vector": None, "graph_boost": None},
        "provenance": {
            "source_key": "legacy-source",
            "raw_snapshot_key": f"raw-{document_key}",
            "import_run_key": "legacy-import",
        },
    }


def _assert_legacy_result_shape(result: dict) -> None:
    assert set(result) == {
        "id",
        "document_key",
        "chunk_key",
        "title",
        "snippet",
        "score",
        "score_components",
        "provenance",
    }
    assert set(result["score_components"]) == {"bm25", "vector", "graph_boost"}
    assert set(result["provenance"]) == {"source_key", "raw_snapshot_key", "import_run_key"}


class _LegacyReadClient:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict]] = []

    def aql(self, query: str, bind_vars: dict) -> list[dict]:
        self.calls.append((query, dict(bind_vars)))
        return [dict(row) for row in self.rows]


def test_v5_does_not_narrow_legacy_text_visibility_or_change_result_envelope() -> None:
    rows = [
        _legacy_result("published-doc", "published", 2.0),
        _legacy_result("draft-doc", "draft", 1.0),
    ]
    client = _LegacyReadClient(rows)
    repository = cast(KnowledgeRepository, type("Repository", (), {"client": client})())

    response = text_search(repository, "legacy query")

    assert set(response) == {"query", "mode", "status", "results"}
    assert (response["query"], response["mode"], response["status"]) == ("legacy query", "text", "ok")
    assert [row["document_key"] for row in response["results"]] == ["published-doc", "draft-doc"]
    for row in response["results"]:
        _assert_legacy_result_shape(row)
    query, bind_vars = client.calls[0]
    assert bind_vars == {"query": "legacy query", "limit": 10, "source_key": None}
    assert "FILTER doc.status" not in query
    assert "@visibility" not in query and "@include_drafts" not in query


class _LegacySemanticClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.results = [
            _legacy_result("published-doc", "published", 0.9),
            _legacy_result("draft-doc", "draft", 0.8),
        ]

    def aql(self, query: str, bind_vars: dict) -> list[dict]:
        self.calls.append((query, dict(bind_vars)))
        if "FOR chunk IN chunks" in query:
            return [
                {
                    "_id": "chunks/published-doc-c0",
                    "_key": "published-doc-c0",
                    "document_key": "published-doc",
                    "text": "published semantic text",
                    "embedding": [1.0, 0.0],
                },
                {
                    "_id": "chunks/draft-doc-c0",
                    "_key": "draft-doc-c0",
                    "document_key": "draft-doc",
                    "text": "draft semantic text",
                    "embedding": [0.0, 1.0],
                },
            ]
        if "FOR item IN @items" in query:
            return [dict(row) for row in self.results]
        raise AssertionError("unexpected legacy semantic query")


def test_v5_does_not_narrow_legacy_semantic_visibility_or_change_result_envelope() -> None:
    client = _LegacySemanticClient()
    repository = cast(KnowledgeRepository, type("Repository", (), {"client": client})())

    response = semantic_search(
        repository,
        "legacy semantic query",
        dimension=2,
        source_key="legacy-source",
        min_similarity=-1.0,
    )

    # source_key forces the full-scan path; honesty contract marks vector as degraded.
    assert set(response) == {"query", "mode", "status", "results", "degraded_components"}
    assert (response["query"], response["mode"], response["status"]) == (
        "legacy semantic query",
        "semantic",
        "degraded",
    )
    assert response["degraded_components"] == ["vector"]
    assert {row["document_key"] for row in response["results"]} == {"published-doc", "draft-doc"}
    for row in response["results"]:
        _assert_legacy_result_shape(row)
    discovery_query, discovery_vars = client.calls[0]
    assert discovery_vars["source_key"] == "legacy-source"
    assert "FILTER doc.status" not in discovery_query
    assert "@visibility" not in discovery_query and "@include_drafts" not in discovery_query


def test_v5_does_not_narrow_legacy_graph_visibility_or_change_result_envelope() -> None:
    rows = [
        _legacy_result("published-doc", "published", 1.0),
        _legacy_result("draft-doc", "draft", 1.0),
    ]
    client = _LegacyReadClient(rows)
    repository = cast(KnowledgeRepository, type("Repository", (), {"client": client})())

    response = graph_neighbors(repository, document="published-doc", limit=2)

    assert set(response) == {"query", "mode", "status", "results"}
    assert (response["query"], response["mode"], response["status"]) == (
        "documents/published-doc",
        "graph",
        "ok",
    )
    assert [row["document_key"] for row in response["results"]] == ["published-doc", "draft-doc"]
    for row in response["results"]:
        _assert_legacy_result_shape(row)
    query, bind_vars = client.calls[0]
    assert "status" not in bind_vars and "visibility" not in bind_vars and "include_drafts" not in bind_vars
    assert "FILTER doc.status" not in query


def test_semantic_search_marks_full_scan_fallback_as_degraded() -> None:
    # source_key cannot use the ANN index, so the Python full-scan path must be honest.
    client = _LegacySemanticClient()
    repository = cast(KnowledgeRepository, type("Repository", (), {"client": client})())

    response = semantic_search(repository, "q", dimension=2, source_key="src", min_similarity=-1.0)

    assert response["status"] == "degraded"
    assert response["degraded_components"] == ["vector"]
    assert len(response["results"]) == 2


class _EmptyScopedSemanticClient:
    def aql(self, query: str, bind_vars: dict) -> list[dict]:
        if "FOR chunk IN chunks" in query:
            assert bind_vars.get("source_key") == "legacy-source"
            return []
        raise AssertionError("unexpected scoped semantic query")


def test_semantic_search_scoped_empty_chunks_reports_vector_degraded() -> None:
    # Scoped semantic always uses fallback; empty embeddings must not look like ok vector search.
    repository = cast(
        KnowledgeRepository,
        type("Repository", (), {"client": _EmptyScopedSemanticClient()})(),
    )

    response = semantic_search(
        repository,
        "legacy semantic query",
        dimension=2,
        source_key="legacy-source",
        min_similarity=-1.0,
    )

    assert response["status"] == "degraded"
    assert response["degraded_components"] == ["vector"]
    assert response["results"] == []
