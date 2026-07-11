import pytest

import knowledge_base.retrieval as retrieval
from knowledge_base.arango import ArangoClient, ArangoError
from knowledge_base.config import load_settings
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.retrieval import (
    _aggregate_community_scores,
    _graph_only_candidates,
    _graph_only_row,
    global_search,
    local_search,
)


def _candidate(key: str, score: float, provenance: dict | None = None) -> dict:
    return {"document_key": key, "score": score, "title": key.upper(), "provenance": provenance or {"source_key": "s"}}


def test_aggregate_community_scores_ranks_by_summed_relevance() -> None:
    # A community's score is the sum of its matched candidates' hybrid scores, so the community with
    # several strong hits outranks one with a single hit (GR-5 global relevance).
    candidates = [_candidate("d1", 0.9), _candidate("d2", 0.8), _candidate("d3", 0.7), _candidate("d4", 0.1)]
    membership = [
        {"doc": "d1", "community": "communities/c-arch"},
        {"doc": "d2", "community": "communities/c-arch"},
        {"doc": "d4", "community": "communities/c-arch"},
        {"doc": "d3", "community": "communities/c-mgmt"},
        {"doc": "dX", "community": "communities/c-arch"},  # not among candidates -> ignored
    ]
    communities = {
        "communities/c-arch": {"_key": "c-arch", "size": 3, "summary": "arch", "top_topics": ["Databases"]},
        "communities/c-mgmt": {"_key": "c-mgmt", "size": 2, "summary": "mgmt", "top_topics": ["Leadership"]},
    }
    ranked = _aggregate_community_scores(candidates, membership, communities, community_limit=5, docs_per_community=2)
    assert [c["community_key"] for c in ranked] == ["c-arch", "c-mgmt"]  # 1.8 (0.9+0.8+0.1) > 0.7
    arch = ranked[0]
    assert arch["score"] == pytest.approx(1.8)
    assert arch["matched_documents"] == 3
    assert [d["document_key"] for d in arch["documents"]] == ["d1", "d2"]  # top-2 by score (docs_per_community=2)
    assert arch["top_topics"] == ["Databases"]
    assert all(d["provenance"] for d in arch["documents"])


def test_aggregate_community_scores_skips_unknown_communities_and_truncates() -> None:
    candidates = [_candidate("d1", 0.9), _candidate("d2", 0.5)]
    membership = [
        {"doc": "d1", "community": "communities/known"},
        {"doc": "d2", "community": "communities/missing"},  # not in the communities map -> dropped
    ]
    communities = {"communities/known": {"_key": "known", "size": 1, "summary": "k", "top_topics": []}}
    ranked = _aggregate_community_scores(candidates, membership, communities, community_limit=5, docs_per_community=5)
    assert [c["community_key"] for c in ranked] == ["known"]
    # community_limit caps the number of communities returned.
    assert len(_aggregate_community_scores(candidates, membership, communities, community_limit=0, docs_per_community=5)) == 0


def test_aggregate_community_scores_empty() -> None:
    assert _aggregate_community_scores([], [], {}, community_limit=5, docs_per_community=5) == []


def test_aggregate_community_scores_breaks_ties_by_community_key_ascending() -> None:
    # Equal summed scores must break ties by community_key ascending, matching the sibling AQL
    # helpers' `... ASC` tie-breaks (deterministic, module-consistent).
    candidates = [_candidate("d1", 0.5), _candidate("d2", 0.5)]
    membership = [
        {"doc": "d1", "community": "communities/zzz"},
        {"doc": "d2", "community": "communities/aaa"},
    ]
    communities = {
        "communities/zzz": {"_key": "zzz", "size": 1, "summary": "z", "top_topics": []},
        "communities/aaa": {"_key": "aaa", "size": 1, "summary": "a", "top_topics": []},
    }
    ranked = _aggregate_community_scores(candidates, membership, communities, community_limit=5, docs_per_community=5)
    assert [c["community_key"] for c in ranked] == ["aaa", "zzz"]


def test_graph_only_row_clamps_score_and_marks_expanded() -> None:
    # GR-3c: a graph-only candidate's score is its connection weight clamped to the ceiling (the
    # weakest real hit's score, itself <= the cap), and it is flagged graph_expanded.
    row = _graph_only_row(
        {"document_key": "d9", "title": "D9", "weight": 0.92, "provenance": {"source_key": "s", "url": None}},
        ceiling=0.3,
    )
    assert row["graph_expanded"] is True
    assert row["score"] == pytest.approx(0.3)  # weight 0.92 clamped down to the 0.3 ceiling
    assert row["score_components"] == {"bm25": None, "vector": None, "graph_boost": pytest.approx(0.3)}
    assert row["document_key"] == "d9" and row["provenance"]["source_key"] == "s"
    # A weight below the ceiling is kept as-is.
    low = _graph_only_row({"document_key": "d8", "title": "D8", "weight": 0.1, "provenance": {}}, ceiling=0.5)
    assert low["score"] == pytest.approx(0.1)


def test_graph_only_candidates_clamps_to_weakest_real_hit(monkeypatch) -> None:
    # GR-3c: the ceiling is min(cap, weakest real hit score) — NOT just the cap. When the weakest real
    # hit scores below the cap, a strongly-connected neighbour is clamped down to that score so it can
    # never outrank a real hit (and the list stays monotonic). Guards the load-bearing clamp: a
    # `ceiling=cap` regression would let a neighbour with weight in (weakest_real, cap] outrank it.
    fused = [
        {"document_key": "r1", "score": 0.62},
        {"document_key": "r2", "score": 0.41},
        {"document_key": "r3", "score": 0.35},  # weakest real hit, below the 0.5 cap
    ]
    captured: dict = {}

    def _fake_related(repository, anchors, *, limit, source_key=None, exclude=None):
        captured.update(anchors=anchors, limit=limit, source_key=source_key, exclude=exclude)
        return [
            {"document_key": "n1", "title": "N1", "weight": 0.47, "provenance": {"source_key": "s"}},
            {"document_key": "n2", "title": "N2", "weight": 0.20, "provenance": {"source_key": "s"}},
        ]

    monkeypatch.setattr(retrieval, "_related_documents", _fake_related)
    repository = KnowledgeRepository(ArangoClient(load_settings()))

    rows = _graph_only_candidates(repository, fused, limit=6, source_key="src")
    assert captured["limit"] == 3  # slots = limit - len(fused)
    assert captured["source_key"] == "src"
    assert captured["exclude"] == ["r1", "r2", "r3"]  # the whole pool is excluded
    assert captured["anchors"] == ["r1", "r2", "r3"]  # anchors are the top hits (<= seed_count)
    # n1's weight 0.47 (> the weakest real hit 0.35, < cap 0.5) is clamped to 0.35 so it cannot
    # outrank r3; a `ceiling=cap` regression would leave it at 0.47 and fail here.
    assert rows[0]["document_key"] == "n1" and rows[0]["score"] == pytest.approx(0.35)
    # n2's weight 0.20 is below the ceiling, kept as-is — distinct score preserves inter-neighbour order.
    assert rows[1]["document_key"] == "n2" and rows[1]["score"] == pytest.approx(0.20)
    assert all(row["graph_expanded"] for row in rows)
    # No empty slots (len(fused) == limit) -> no expansion at all.
    assert _graph_only_candidates(repository, fused, limit=3, source_key=None) == []


def test_local_and_global_search_degrade_when_retrieval_raises(monkeypatch) -> None:
    # The never-throw contract must hold for the initial retrieval too: if hybrid_search raises
    # (DB/vector store down), local/global return a well-formed degraded contract, not an exception
    # (PR #32 review).
    def _boom(*args, **kwargs):
        raise ArangoError("db down")

    monkeypatch.setattr(retrieval, "hybrid_search", _boom)
    # A real repository object (never connected — hybrid_search raises before it is touched).
    repository = KnowledgeRepository(ArangoClient(load_settings()))

    local = local_search(repository, "q")
    assert local["status"] == "degraded"
    assert "retrieval" in local["degraded_components"]
    assert local["mode"] == "graphrag-local"
    assert local["seeds"] == [] and local["communities"] == [] and local["entities"] == []

    result = global_search(repository, "q")
    assert result["status"] == "degraded"
    assert "retrieval" in result["degraded_components"]
    assert result["mode"] == "graphrag-global"
    assert result["communities"] == []
