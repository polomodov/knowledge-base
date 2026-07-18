from typing import Any, cast

import pytest

import knowledge_base.indexing as indexing
from knowledge_base.config import Settings
from knowledge_base.embeddings import HashEmbeddingProvider
from knowledge_base.indexing import EmbeddingRebuildError, _ann_related, _scored_candidates, _select_related, build_embeddings
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


class _FakeProvider:
    model = "new-model-v1"
    dimension = 16

    def embed(self, text: str) -> list[float]:
        return [0.0] * self.dimension


def _settings() -> Settings:
    return Settings(embedding_dimension=16, embedding_provider="hash")


def test_build_embeddings_stages_before_touching_the_live_index(monkeypatch: pytest.MonkeyPatch) -> None:
    # Crash-safety invariant: the long re-embed writes shadow fields first; the live vector index is
    # only dropped/rebuilt in the short swap phase, AFTER staging proves complete.
    events: list[str] = []
    monkeypatch.setattr(indexing, "build_embedding_provider", lambda settings: _FakeProvider())
    monkeypatch.setattr(indexing, "_stage_pending_embeddings", lambda repo, provider, **k: (events.append("stage"), 3)[1])
    monkeypatch.setattr(indexing, "_count_chunks", lambda repo: (events.append("count"), 3)[1])
    monkeypatch.setattr(indexing, "_swap_pending_embeddings", lambda repo: (events.append("swap"), 3)[1])
    monkeypatch.setattr(
        indexing, "ensure_vector_index", lambda client, *, dimension: (events.append(f"index:{dimension}"), {"status": "ok"})[1]
    )
    monkeypatch.setattr(indexing, "_clear_related_edges", lambda repo, ids, *, scoped: (events.append("clear_related"), 2)[1])

    class _Client:
        def drop_index(self, collection: str, name: str) -> dict[str, Any]:
            events.append("drop_index")
            return {"name": name, "dropped": True}

    class _Repo:
        client = _Client()

    result = build_embeddings(cast(KnowledgeRepository, _Repo()), _settings())

    assert events == ["stage", "count", "drop_index", "swap", "index:16", "clear_related"]
    assert result == {
        "chunks": 3,
        "model": "new-model-v1",
        "dimension": 16,
        "vector_index": {"status": "ok"},
        "related_edges_removed": 2,
    }


def test_build_embeddings_aborts_and_rolls_back_on_incomplete_staging(monkeypatch: pytest.MonkeyPatch) -> None:
    # If staging does not cover the whole corpus, never drop the live index or promote a partial
    # re-embed: roll the shadow fields back and fail loudly.
    events: list[str] = []
    monkeypatch.setattr(indexing, "build_embedding_provider", lambda settings: _FakeProvider())
    monkeypatch.setattr(indexing, "_stage_pending_embeddings", lambda repo, provider, **k: 2)  # only 2 staged
    monkeypatch.setattr(indexing, "_count_chunks", lambda repo: 5)  # but 5 chunks exist
    monkeypatch.setattr(indexing, "_clear_pending_embeddings", lambda repo: events.append("clear_pending"))
    monkeypatch.setattr(indexing, "_swap_pending_embeddings", lambda repo: (events.append("swap"), 0)[1])

    class _Client:
        def drop_index(self, collection: str, name: str) -> dict[str, Any]:
            events.append("drop_index")
            return {}

    class _Repo:
        client = _Client()

    with pytest.raises(EmbeddingRebuildError):
        build_embeddings(cast(KnowledgeRepository, _Repo()), _settings())

    assert events == ["clear_pending"]  # rolled back; the live index and swap were never touched


def test_stage_pending_embeddings_writes_only_shadow_fields() -> None:
    # Staging must not touch the live `embedding`/`embedding_model` so retrieval keeps serving the old
    # space during the rebuild.
    captured: list[str] = []

    class _Client:
        def aql(self, query: str, bind_vars: dict | None = None) -> list[dict]:
            captured.append(query)
            if "SORT c._key" in query:
                assert bind_vars is not None
                return [{"key": "c1", "text": "alpha"}] if bind_vars["offset"] == 0 else []
            return []

    class _Repo:
        client = _Client()

    staged = indexing._stage_pending_embeddings(cast(KnowledgeRepository, _Repo()), HashEmbeddingProvider(dimension=8))

    assert staged == 1
    update = next(query for query in captured if "UPDATE item.key" in query)
    assert "embedding_pending" in update and "embedding_model_pending" in update
    # Live fields are never assigned during staging.
    assert "embedding:" not in update and "embedding_model:" not in update
