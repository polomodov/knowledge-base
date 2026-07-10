from typing import cast

from knowledge_base.indexing import _ann_related, _scored_candidates, _select_related
from knowledge_base.repository import KnowledgeRepository


class _FakeAnnClient:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.windows: list[int] = []

    def aql(self, query: str, bind_vars: dict | None = None) -> list[dict]:
        assert bind_vars is not None
        window = bind_vars["window"]
        self.windows.append(window)
        return [dict(row) for row in self.rows[:window]]


class _FakeAnnRepository:
    def __init__(self, rows: list[dict]) -> None:
        self.client = _FakeAnnClient(rows)


def _chunk(chunk_id: str, document_key: str, *, model: str = "hash-v1") -> dict:
    return {"id": chunk_id, "document_key": document_key, "embedding_model": model}


def _candidate(chunk_id: str, document_key: str, score: float, *, model: str = "hash-v1") -> dict:
    return {"id": chunk_id, "document_key": document_key, "embedding_model": model, "score": score}


def test_select_related_keeps_valid_cross_document_neighbours() -> None:
    chunk = _chunk("chunks/a1", "A")
    candidates = [
        _candidate("chunks/b1", "B", 0.9),  # valid
        _candidate("chunks/c1", "C", 0.3),  # below threshold
        _candidate("chunks/a2", "A", 1.0),  # same document
        _candidate("chunks/a1", "A", 1.0),  # self
    ]
    assert _select_related(chunk, candidates, top_k=5, min_score=0.5) == [("chunks/b1", 0.9)]


def test_select_related_excludes_incompatible_models() -> None:
    chunk = _chunk("chunks/a1", "A", model="model-x")
    candidates = [_candidate("chunks/b1", "B", 1.0, model="model-y")]  # different model = incompatible space
    assert _select_related(chunk, candidates, top_k=5, min_score=0.5) == []


def test_select_related_respects_top_k_and_orders_by_score_then_id() -> None:
    chunk = _chunk("chunks/a1", "A")
    candidates = [
        _candidate("chunks/b1", "B", 0.8),
        _candidate("chunks/c1", "C", 0.9),
        _candidate("chunks/d1", "D", 0.9),  # ties with c1 -> id breaks the tie (c1 before d1)
    ]
    assert _select_related(chunk, candidates, top_k=2, min_score=0.5) == [("chunks/c1", 0.9), ("chunks/d1", 0.9)]


def test_ann_related_grows_window_past_invalid_neighbours() -> None:
    # The first 50 ANN rows are all same-document (invalid); the only valid cross-document neighbour
    # sits just outside that window. The builder must grow the window instead of returning nothing.
    chunk = {"id": "chunks/a1", "document_key": "A", "embedding_model": "m", "embedding": [0.0, 0.0]}
    rows = [{"id": f"chunks/a-{i}", "document_key": "A", "embedding_model": "m", "score": 0.99} for i in range(50)]
    rows.append({"id": "chunks/b1", "document_key": "B", "embedding_model": "m", "score": 0.95})
    repository = _FakeAnnRepository(rows)

    selected = _ann_related(cast(KnowledgeRepository, repository), chunk, top_k=1, min_score=0.5)

    assert selected == [("chunks/b1", 0.95)]
    assert repository.client.windows == [50, 200]  # grew once past the 50 invalid same-document rows


def test_scored_candidates_scores_pool_and_drops_self() -> None:
    chunk = {"id": "chunks/a1", "document_key": "A", "embedding_model": "m", "embedding": [1.0, 0.0]}
    pool = [
        chunk,
        {"id": "chunks/b1", "document_key": "B", "embedding_model": "m", "embedding": [1.0, 0.0]},  # cosine 1.0
        {"id": "chunks/c1", "document_key": "C", "embedding_model": "m", "embedding": [0.0, 1.0]},  # cosine 0.0
    ]
    scored = {candidate["id"]: candidate["score"] for candidate in _scored_candidates(chunk, pool)}
    assert scored == {"chunks/b1": 1.0, "chunks/c1": 0.0}  # self excluded
