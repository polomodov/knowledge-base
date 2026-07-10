import itertools
import os

import pytest

from knowledge_base.arango import ArangoClient
from knowledge_base.config import load_settings
from knowledge_base.embeddings import hash_embedding
from knowledge_base.fixture import ingest_fixture
from knowledge_base.indexing import build_related_edges, rebuild_indexes
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.retrieval import graph_neighbors, hybrid_search, semantic_search, text_search
from knowledge_base.schema import bootstrap_schema

pytestmark = pytest.mark.integration


def _integration_enabled() -> bool:
    return os.getenv("KB_RUN_INTEGRATION") == "1"


@pytest.mark.skipif(not _integration_enabled(), reason="set KB_RUN_INTEGRATION=1 with ArangoDB running")
def test_fixture_pipeline_end_to_end() -> None:
    settings = load_settings()
    client = ArangoClient(settings)
    repository = KnowledgeRepository(client)

    bootstrap_schema(client)
    ingest_result = ingest_fixture(repository, settings)
    created_before = _document_created_at(repository)
    dedupe_result = ingest_fixture(repository, settings)
    created_after = _document_created_at(repository)
    index_result = rebuild_indexes(repository, target="all")
    text = text_search(repository, "systems thinking")
    no_match = text_search(repository, "zzzxqvnomatch928371")
    semantic = semantic_search(repository, "ideas across books", dimension=settings.embedding_dimension)
    topic_graph = graph_neighbors(repository, topic="systems-thinking")
    author_graph = graph_neighbors(repository, author="fixture-author")
    work_graph = graph_neighbors(repository, work="fixture-work-knowledge-graphs")
    missing_graph = graph_neighbors(repository, topic="missing-topic")
    hybrid = hybrid_search(repository, "systems thinking writing workflow", dimension=settings.embedding_dimension)

    assert ingest_result["status"] == "ok"
    assert dedupe_result["created"]["documents"] == 0
    assert dedupe_result["created"]["chunks"] == 0
    # created_at is immutable across re-ingest (finding #11).
    assert created_before
    assert created_after == created_before
    # deduplicated is computed from real totals, not a hardcoded flag (finding #35):
    # created + deduplicated == total, and a second identical ingest deduplicates
    # everything. Kept independent of any pre-existing DB state.
    for key in ("documents", "chunks"):
        total = ingest_result["created"][key] + ingest_result["deduplicated"][key]
        assert total >= 1
        assert dedupe_result["created"][key] == 0
        assert dedupe_result["deduplicated"][key] == total
    assert index_result["status"] == "ok"
    assert text["results"]
    assert no_match["results"] == []
    # text and semantic each return at most one row per document (finding #14).
    assert _unique_document_keys(text["results"])
    # Results are ranked by descending score, not merely present (finding #45).
    assert _monotonic_non_increasing([result["score"] for result in text["results"]])
    # The fixture document (source fixture-notebook) matches "systems thinking" and is ranked.
    assert any(result["provenance"]["source_key"] == "fixture-notebook" for result in text["results"])
    _assert_provenance(text["results"])
    assert semantic["status"] in {"ok", "degraded"}
    assert _unique_document_keys(semantic["results"])
    _assert_provenance(semantic["results"])
    assert topic_graph["status"] == "ok"
    assert author_graph["status"] == "ok"
    assert work_graph["status"] == "ok"
    assert missing_graph["status"] == "ok"
    assert missing_graph["results"] == []
    assert {result["kind"] for result in topic_graph["results"]} & {"document", "chunk", "topic", "author", "work"}
    assert {result["kind"] for result in author_graph["results"]} & {"document", "topic", "work"}
    assert {result["kind"] for result in work_graph["results"]} & {"document", "topic", "author"}
    # Each graph vertex is returned once (finding #13).
    for graph_result in (topic_graph, author_graph, work_graph):
        ids = [result["id"] for result in graph_result["results"]]
        assert len(ids) == len(set(ids))
    # Source filter stays consistent with the unfiltered branch and still dedups (finding #18).
    filtered_topic = graph_neighbors(repository, topic="systems-thinking", source_key="fixture-notebook")
    assert filtered_topic["status"] == "ok"
    assert filtered_topic["results"]
    assert len({result["id"] for result in filtered_topic["results"]}) == len(filtered_topic["results"])
    assert all(result["provenance"]["source_key"] == "fixture-notebook" for result in filtered_topic["results"])
    _assert_provenance(topic_graph["results"])
    assert hybrid["status"] in {"ok", "degraded"}
    assert hybrid["results"]
    assert {"bm25", "vector", "graph_boost"} <= set(hybrid["results"][0]["score_components"])
    # One row per document (finding #14), descending scores, no negative fused scores (findings #45, #16).
    assert _unique_document_keys(hybrid["results"])
    assert _monotonic_non_increasing([result["score"] for result in hybrid["results"]])
    assert all(result["score"] >= 0 for result in hybrid["results"])
    # graph_boost is now a real, bounded graph signal (GR-1): a number in [0, cap] when the
    # graph component is healthy, and null only if the graph lookup degraded.
    graph_degraded = "graph" in hybrid.get("degraded_components", [])
    for result in hybrid["results"]:
        boost = result["score_components"]["graph_boost"]
        if graph_degraded:
            assert boost is None
        else:
            assert isinstance(boost, (int, float))
            assert 0.0 <= boost <= 0.5
    _assert_provenance(hybrid["results"])


@pytest.mark.skipif(not _integration_enabled(), reason="set KB_RUN_INTEGRATION=1 with ArangoDB running")
def test_graph_source_filter_keeps_cross_source_shared_vertices() -> None:
    # A topic/author shared across sources must not become a false negative under a
    # source filter just because it was first reached via another source's document
    # (finding #18 / PR #7 review): dedup happens after the source filter, not during
    # the traversal.
    settings = load_settings()
    repository = KnowledgeRepository(ArangoClient(settings))
    bootstrap_schema(repository.client)

    now = "2026-07-07T00:00:00Z"
    topic = "audit-shared-topic"
    author = "audit-shared-author"
    sources = ("audit-src-a", "audit-src-b")
    repository.upsert("topics", {"_key": topic, "label": "Audit Shared Topic"})
    repository.upsert("authors", {"_key": author, "display_name": "Audit Shared Author"})
    for source_key in sources:
        repository.upsert(
            "sources",
            {"_key": source_key, "type": "test", "display_name": source_key, "created_at": now},
        )
        document_key_value = f"audit-doc-{source_key}"
        repository.upsert(
            "documents",
            {
                "_key": document_key_value,
                "source_key": source_key,
                "canonical_id": document_key_value,
                "title": f"Doc {source_key}",
                "text": "shared audit document",
                "url": None,
                "created_at": now,
            },
        )
        for collection, target in (
            ("document_mentions_topic", f"topics/{topic}"),
            ("document_mentions_author", f"authors/{author}"),
        ):
            repository.upsert_edge(
                collection,
                {
                    "_key": f"edge-{document_key_value}-{collection}",
                    "_from": f"documents/{document_key_value}",
                    "_to": target,
                    "import_run_key": "audit",
                    "provenance": {"raw_snapshot_key": "audit"},
                },
            )

    # The shared author is reachable from the topic via both sources' documents.
    # Filtering by either source must still surface it, scoped to that source only.
    for source_key in sources:
        result = graph_neighbors(repository, topic=topic, source_key=source_key)
        assert result["status"] == "ok"
        entity_keys = {row["entity_key"] for row in result["results"]}
        assert author in entity_keys, f"shared author missing under source {source_key}"
        assert f"audit-doc-{source_key}" in entity_keys
        assert all(row["provenance"]["source_key"] == source_key for row in result["results"])
        ids = [row["id"] for row in result["results"]]
        assert len(ids) == len(set(ids))


@pytest.mark.skipif(not _integration_enabled(), reason="set KB_RUN_INTEGRATION=1 with ArangoDB running")
def test_hybrid_graph_boost_rewards_shared_entities() -> None:
    # GR-1: hybrid ranking folds in a graph signal. Three documents match the query equally by
    # text/vector; two of them share a topic and reinforce each other, while the isolated one
    # shares nothing and gets no boost, so it ranks last.
    settings = load_settings()
    repository = KnowledgeRepository(ArangoClient(settings))
    bootstrap_schema(repository.client)

    now = "2026-07-08T00:00:00Z"
    source_key = "gb-src"
    topic = "graphrag-shared-topic"
    body = "graphrag retrieval quality and ranking"
    repository.upsert("sources", {"_key": source_key, "type": "test", "display_name": source_key, "created_at": now})
    repository.upsert("topics", {"_key": topic, "label": "GraphRAG Shared Topic"})

    for document_key_value, shares_topic in (("gb-seed", True), ("gb-connected", True), ("gb-isolated", False)):
        repository.upsert(
            "documents",
            {
                "_key": document_key_value,
                "source_key": source_key,
                "canonical_id": document_key_value,
                "title": document_key_value,
                "text": body,
                "url": None,
                "created_at": now,
            },
        )
        chunk_key_value = f"{document_key_value}-c0"
        repository.upsert(
            "chunks",
            {
                "_key": chunk_key_value,
                "document_key": document_key_value,
                "ordinal": 0,
                "text": body,
                "embedding": hash_embedding(body, dimension=settings.embedding_dimension),
            },
        )
        repository.upsert_edge(
            "chunk_of_document",
            {"_key": f"edge-{chunk_key_value}", "_from": f"chunks/{chunk_key_value}", "_to": f"documents/{document_key_value}"},
        )
        if shares_topic:
            repository.upsert_edge(
                "document_mentions_topic",
                {
                    "_key": f"edge-{document_key_value}-topic",
                    "_from": f"documents/{document_key_value}",
                    "_to": f"topics/{topic}",
                    "import_run_key": "gb",
                    "method": "test",
                    "evidence": topic,
                },
            )

    rebuild_indexes(repository, target="all")
    hybrid = hybrid_search(repository, "graphrag retrieval quality", dimension=settings.embedding_dimension)

    boosts = {row["document_key"]: row["score_components"]["graph_boost"] for row in hybrid["results"]}
    assert {"gb-seed", "gb-connected", "gb-isolated"} <= set(boosts)
    assert boosts["gb-seed"] > 0  # reinforced by the topic it shares with gb-connected
    assert boosts["gb-connected"] > 0  # reinforced by the topic it shares with gb-seed
    assert boosts["gb-isolated"] == 0  # shares no entity -> no graph boost
    order = [row["document_key"] for row in hybrid["results"]]
    assert order.index("gb-connected") < order.index("gb-isolated")


@pytest.mark.skipif(not _integration_enabled(), reason="set KB_RUN_INTEGRATION=1 with ArangoDB running")
def test_related_edges_link_similar_cross_document_chunks() -> None:
    # GR-3: build_related_edges populates item_related_to_item with cross-document similarity
    # edges, turning the provenance tree into a knowledge graph. Two documents with identical text
    # get identical chunk embeddings (cosine 1.0), so an undirected edge links them and graph
    # neighbours of one document surface the other.
    settings = load_settings()
    repository = KnowledgeRepository(ArangoClient(settings))
    bootstrap_schema(repository.client)

    now = "2026-07-08T00:00:00Z"
    source_key = "rel-src"
    body = "distributed systems consensus and replication"
    repository.upsert("sources", {"_key": source_key, "type": "test", "display_name": source_key, "created_at": now})
    for document_key_value in ("rel-doc-a", "rel-doc-b"):
        repository.upsert(
            "documents",
            {
                "_key": document_key_value,
                "source_key": source_key,
                "canonical_id": document_key_value,
                "title": document_key_value,
                "text": body,
                "url": None,
                "created_at": now,
            },
        )
        chunk_key_value = f"{document_key_value}-c0"
        repository.upsert(
            "chunks",
            {
                "_key": chunk_key_value,
                "document_key": document_key_value,
                "ordinal": 0,
                "text": body,
                "embedding": hash_embedding(body, dimension=settings.embedding_dimension),
                "embedding_model": "hash-v1",
            },
        )
        repository.upsert_edge(
            "chunk_of_document",
            {
                "_key": f"rel-edge-{chunk_key_value}",
                "_from": f"chunks/{chunk_key_value}",
                "_to": f"documents/{document_key_value}",
            },
        )

    # Scope to this test's source so the build stays isolated from the rest of the corpus.
    result = build_related_edges(repository, top_k=5, min_score=0.5, source_key=source_key)
    assert result["created"] >= 1  # at least the a<->b cross-document pair

    related = repository.client.aql(
        """
        FOR e IN item_related_to_item
          FILTER e._from IN [@a, @b] AND e._to IN [@a, @b]
          RETURN e
        """,
        {"a": "chunks/rel-doc-a-c0", "b": "chunks/rel-doc-b-c0"},
    )
    assert related
    assert related[0]["method"] == "embedding-similarity"
    assert related[0]["weight"] >= 0.5

    # Graph neighbours of document A now surface document B via the similarity edge.
    neighbours = graph_neighbors(repository, document="rel-doc-a", documents_only=True)
    assert neighbours["status"] == "ok"
    assert "rel-doc-b" in {row["document_key"] for row in neighbours["results"]}

    # Idempotent: re-running adds no new edges.
    assert build_related_edges(repository, top_k=5, min_score=0.5, source_key=source_key)["created"] == 0


def _unique_document_keys(results: list[dict]) -> bool:
    keys = [result["document_key"] for result in results]
    return len(keys) == len(set(keys))


def _monotonic_non_increasing(scores: list[float]) -> bool:
    return all(earlier >= later for earlier, later in itertools.pairwise(scores))


def _document_created_at(repository: KnowledgeRepository) -> dict[str, str]:
    rows = repository.client.aql(
        "FOR d IN documents SORT d._key RETURN {key: d._key, created_at: d.created_at}",
    )
    return {row["key"]: row["created_at"] for row in rows}


def _assert_provenance(results: list[dict]) -> None:
    for result in results:
        provenance = result["provenance"]
        assert provenance["source_key"]
        assert provenance["raw_snapshot_key"]
        assert provenance["import_run_key"]
