import pytest

from knowledge_base.retrieval import (
    _cosine,
    _dedup_best_by_document,
    _graph_boosts,
    _merge_hybrid,
    _start_vertex,
    _vector_ranked,
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
    ranked = _vector_ranked(repository, [0.0] * 8, limit=10, source_key=None, model="hash-v1")

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
    assert _vector_ranked(repository, [0.0] * 8, limit=10, source_key="src", model="hash-v1") is None
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
    ranked = _vector_ranked(repository, [0.0] * 8, limit=10, source_key=None, model="model-x")

    assert ranked is not None
    assert {item["document_key"] for item in ranked} == {"a", "c"}  # the model-y chunk is excluded


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
