from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from knowledge_base.arango import ArangoError
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.research_retrieval import (
    ResearchRetrievalError,
    clean_community_leads,
    hydrate_chunk_candidates,
    lexical_chunk_candidates,
    load_corpus_context,
    related_leads,
    semantic_chunk_candidates,
    topic_leads,
)
from knowledge_base.research_workflow import ResearchRequest, ResearchVisibility

CORPUS_PATH = Path(__file__).parents[1] / "fixtures/research/safe-research-corpus.json"
CORPUS = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
HIDDEN = "HIDDEN_DRAFT_SIGNAL_MUST_NOT_LEAK"
_PRIVATE_MARKER = "must-not-leak"


class FakeProvider:
    model = "hash-v1"
    dimension = 2

    def embed(self, text: str) -> list[float]:
        assert text
        return [1.0, 0.0]


class FakeClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []
        self.settings = SimpleNamespace(
            arango_database="knowledge_base_test",
            retrieval_min_similarity=0.25,
            arango_url="http://localhost:8529",
            arango_user="test-user",
            arango_password=_PRIVATE_MARKER,
        )

    def aql(
        self,
        query: str,
        bind_vars: dict[str, Any] | None = None,
        *,
        batch_size: int | None = None,
    ) -> list[dict[str, Any]]:
        marker = next((value for value in self.responses if value in query), None)
        assert marker is not None, f"unexpected research AQL: {query}"
        self.calls.append({"marker": marker, "query": query, "bind_vars": bind_vars or {}})
        response = self.responses[marker]
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)


class FakeRepository:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.client = FakeClient(responses)


def _as_repository(repository: FakeRepository) -> KnowledgeRepository:
    return cast(KnowledgeRepository, repository)


def test_lexical_scope_precedes_ranking_and_returns_exact_chunk() -> None:
    row = _hydrated_row("research-published-a-c0", bm25=4.5)
    repository = FakeRepository({"research:lexical_chunk_candidates": [row]})

    results = lexical_chunk_candidates(_as_repository(repository), _request())

    assert results[0]["chunk"]["text"] == CORPUS["chunks"][0]["text"]
    assert results[0]["score_components"] == {"lexical": 4.5, "vector": None}
    call = _call(repository, "research:lexical_chunk_candidates")
    _assert_scope(call["bind_vars"])
    filters = (
        "doc.status IN @statuses",
        "@source_key == null OR doc.source_key == @source_key",
        "@published_from == null OR doc.published_at >= @published_from",
        "@published_to_exclusive == null OR doc.published_at < @published_to_exclusive",
    )
    assert max(call["query"].index(value) for value in filters) < call["query"].index("SORT")
    assert 'TOKENS(@query, "text_en")' in call["query"]
    assert 'TOKENS(@query, "text_ru")' in call["query"]


def test_semantic_search_bounds_overfetch_then_scopes_hydration_and_exact_cosine() -> None:
    ann = [
        {"chunk_key": "research-draft-hidden-c0", "approximate_score": 0.999},
        {"chunk_key": "research-published-b-c0", "approximate_score": 0.98},
        {"chunk_key": "research-published-a-c0", "approximate_score": 0.70},
    ]
    repository = FakeRepository(
        {
            "research:semantic_chunk_candidates": ann,
            "research:hydrate_chunk_candidates": [
                _hydrated_row("research-published-b-c0", embedding=[0.8, 0.6]),
                _hydrated_row("research-published-a-c0", embedding=[1.0, 0.0]),
            ],
        }
    )
    request = ResearchRequest(query="synthetic", candidate_limit=2, evidence_limit=2)

    results = semantic_chunk_candidates(_as_repository(repository), request, provider=FakeProvider(), overfetch_factor=3)

    assert [row["chunk"]["_key"] for row in results] == [
        "research-published-a-c0",
        "research-published-b-c0",
    ]
    assert [row["score_components"]["vector"] for row in results] == [1.0, 0.8]
    assert HIDDEN not in json.dumps(results)
    ann_call = _call(repository, "research:semantic_chunk_candidates")
    assert ann_call["bind_vars"]["overfetch_limit"] == 6
    assert ann_call["bind_vars"]["embedding_model"] == "hash-v1"
    hydration = _call(repository, "research:hydrate_chunk_candidates")["bind_vars"]
    assert hydration["statuses"] == ["published"]
    assert set(hydration["chunk_keys"]) == {row["chunk_key"] for row in ann}


def test_hydration_preserves_exact_provenance_and_rejects_wrong_ownership() -> None:
    exact = _hydrated_row("research-published-a-c1", embedding=[1.0, 0.0])
    repository = FakeRepository({"research:hydrate_chunk_candidates": [exact]})

    rows = hydrate_chunk_candidates(_as_repository(repository), [exact["chunk"]["_key"]], _request())

    assert rows == [exact]
    assert rows[0]["raw_edge"]["document_key"] == rows[0]["document"]["_key"]
    assert rows[0]["raw_snapshot"]["source_key"] == rows[0]["document"]["source_key"]

    invalid = deepcopy(exact)
    invalid["raw_snapshot"]["source_key"] = "research-source-b"
    repository = FakeRepository({"research:hydrate_chunk_candidates": [invalid]})
    typed_repository = _as_repository(repository)
    chunk_keys = [invalid["chunk"]["_key"]]
    request = _request()
    with pytest.raises(ResearchRetrievalError, match=r"ownership|provenance"):
        hydrate_chunk_candidates(typed_repository, chunk_keys, request)


def test_graph_leads_filter_visibility_and_suppress_whole_tainted_community() -> None:
    repository = FakeRepository(
        {
            "research:topic_leads": [
                {"topic_key": "systems-thinking", "document_statuses": ["published"]},
                {"topic_key": "hidden-lunar-archive", "document_statuses": ["draft"]},
            ],
            "research:related_leads": [
                {"document_key": "research-published-b", "document_status": "published"},
                {"document_key": "research-draft-hidden", "document_status": "draft"},
            ],
            "research:clean_community_leads": [
                {"community_key": "research-community-clean", "is_clean": True},
                {"community_key": "research-community-tainted", "is_clean": False, "summary": HIDDEN},
            ],
        }
    )
    request = _request()

    typed_repository = _as_repository(repository)
    topics = topic_leads(typed_repository, ["research-published-a"], request, limit=10)
    related = related_leads(typed_repository, ["research-published-a-c0"], request, limit=10)
    communities = clean_community_leads(typed_repository, ["research-published-a"], request, limit=10)

    assert [row["topic_key"] for row in topics] == ["systems-thinking"]
    assert [row["document_key"] for row in related] == ["research-published-b"]
    assert [row["community_key"] for row in communities] == ["research-community-clean"]
    assert HIDDEN not in json.dumps((topics, related, communities))
    for marker in ("research:topic_leads", "research:related_leads", "research:clean_community_leads"):
        _assert_scope(_call(repository, marker)["bind_vars"])
    assert "disallowed_members" in _call(repository, "research:clean_community_leads")["query"]


def test_optional_corpus_context_failure_degrades_without_credential_leak() -> None:
    repository = FakeRepository({"research:corpus_context": ArangoError("index unavailable")})

    context = load_corpus_context(
        _as_repository(repository),
        _request(),
        provider=FakeProvider(),
        built_at="2026-07-12T12:00:00Z",
        git_revision="0123456789abcdef",
    )

    assert context == {
        "database": "knowledge_base_test",
        "built_at": "2026-07-12T12:00:00Z",
        "embedding_model": "hash-v1",
        "embedding_dimension": 2,
        "retrieval_min_similarity": 0.25,
        "latest_import_run_key": None,
        "latest_index_runs": {},
        "git_revision": "0123456789abcdef",
        "warnings": ["optional corpus/index freshness context is unavailable"],
    }
    assert not {"arango_url", "arango_user", "arango_password"} & context.keys()
    assert _PRIVATE_MARKER not in json.dumps(context)


def _request() -> ResearchRequest:
    return ResearchRequest(
        query="synthetic evidence",
        source_key="research-source-a",
        published_from="2026-01-01",
        published_to="2026-02-28",
        visibility=ResearchVisibility.PUBLISHED_ONLY,
    )


def _assert_scope(bind_vars: dict[str, Any]) -> None:
    assert bind_vars["statuses"] == ["published"]
    assert bind_vars["source_key"] == "research-source-a"
    assert bind_vars["published_from"] == "2026-01-01T00:00:00Z"
    assert bind_vars["published_to_exclusive"] == "2026-03-01T00:00:00Z"


def _call(repository: FakeRepository, marker: str) -> dict[str, Any]:
    return next(call for call in repository.client.calls if call["marker"] == marker)


def _hydrated_row(
    chunk_key: str,
    *,
    embedding: list[float] | None = None,
    bm25: float | None = None,
) -> dict[str, Any]:
    chunk = deepcopy(next(row for row in CORPUS["chunks"] if row["_key"] == chunk_key))
    document = deepcopy(next(row for row in CORPUS["documents"] if row["_key"] == chunk["document_key"]))
    raw_edge = deepcopy(next(row for row in CORPUS["edges"]["chunk_derived_from_raw"] if row["_from"].endswith(chunk_key)))
    raw_key = raw_edge["_to"].split("/", 1)[1]
    raw = deepcopy(next(row for row in CORPUS["raw_snapshots"] if row["_key"] == raw_key))
    source_edge = deepcopy(
        next(row for row in CORPUS["edges"]["document_from_source"] if row["_from"].endswith(document["_key"]))
    )
    if embedding is not None:
        chunk.update(embedding=embedding, embedding_model="hash-v1")
    result = {
        "chunk": chunk,
        "document": document,
        "raw_edge": raw_edge,
        "raw_snapshot": {key: raw[key] for key in ("_key", "source_key", "captured_at")},
        "source_edge": source_edge,
    }
    if bm25 is not None:
        result["bm25"] = bm25
    return result
