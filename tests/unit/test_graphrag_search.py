import pytest

from knowledge_base.retrieval import _aggregate_community_scores


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
