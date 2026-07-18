import contextlib
import dataclasses
import itertools
import os
import time
from typing import cast

import pytest

from knowledge_base import indexing
from knowledge_base.arango import ArangoClient, ArangoError
from knowledge_base.config import Settings, load_settings
from knowledge_base.embeddings import hash_embedding
from knowledge_base.fixture import ingest_fixture
from knowledge_base.indexing import EmbeddingRebuildError, build_communities, build_related_edges, rebuild_indexes
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.retrieval import (
    global_search,
    graph_neighbors,
    hybrid_search,
    local_search,
    semantic_search,
    text_search,
)
from knowledge_base.schema import bootstrap_schema

pytestmark = pytest.mark.integration


def _integration_enabled() -> bool:
    return os.getenv("KB_RUN_INTEGRATION") == "1"


def _vector_index_present(client: ArangoClient) -> bool:
    response = client.request("GET", "/_api/index?collection=chunks", database=client.settings.arango_database)
    return any(index.get("name") == "idx_chunks_embedding_vector" for index in response.get("indexes", []))


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

    # Rebuild is a full refresh (clear-then-insert): the edge set is stable, not duplicated.
    second = build_related_edges(repository, top_k=5, min_score=0.5, source_key=source_key)
    assert second["created"] == result["created"]
    assert second["removed"] == result["created"]  # cleared exactly what the previous build owned
    related_again = repository.client.aql(
        """
        FOR e IN item_related_to_item
          FILTER e._from IN [@a, @b] AND e._to IN [@a, @b]
          RETURN e
        """,
        {"a": "chunks/rel-doc-a-c0", "b": "chunks/rel-doc-b-c0"},
    )
    assert len(related_again) == len(related)  # still one a<->b edge, no duplication


@pytest.mark.skipif(not _integration_enabled(), reason="set KB_RUN_INTEGRATION=1 with ArangoDB running")
def test_related_edges_boost_hybrid_ranking() -> None:
    # GR-3b: an item_related_to_item link to a strong candidate lifts a document in hybrid ranking,
    # the same way a shared topic does (GR-1). Three documents match the query equally; two are
    # similarity-linked and reinforce each other, the isolated one is not and ranks last.
    settings = load_settings()
    repository = KnowledgeRepository(ArangoClient(settings))
    bootstrap_schema(repository.client)

    now = "2026-07-10T00:00:00Z"
    source_key = "rr-src"
    body = "graphrag related-ranking probeword consensus"  # rare 'probeword' keeps these docs in the pool
    repository.upsert("sources", {"_key": source_key, "type": "test", "display_name": source_key, "created_at": now})
    for document_key_value in ("rr-seed", "rr-related", "rr-isolated"):
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
                "_key": f"rr-edge-{chunk_key_value}",
                "_from": f"chunks/{chunk_key_value}",
                "_to": f"documents/{document_key_value}",
            },
        )
    # Similarity link between seed and related only (no topics anywhere, so the boost is purely
    # the item_related_to_item signal).
    repository.upsert_edge(
        "item_related_to_item",
        {
            "_key": "rel-rr-seed-related",
            "_from": "chunks/rr-seed-c0",
            "_to": "chunks/rr-related-c0",
            "weight": 0.9,
            "method": "embedding-similarity",
            "created_at": now,
        },
    )
    # A non-derived edge (different method, no numeric weight) must be ignored by ranking (PR #26
    # review): it must neither crash hybrid nor boost the otherwise-isolated document.
    repository.upsert_edge(
        "item_related_to_item",
        {"_key": "manual-rr-isolated-seed", "_from": "chunks/rr-isolated-c0", "_to": "chunks/rr-seed-c0", "method": "manual"},
    )

    def _boosts_applied(hybrid: dict) -> bool:
        # Wait until BM25 scores stabilize enough for rr-seed/rr-related to be seeds and get boosted.
        graph_boost = {row["document_key"]: row["score_components"]["graph_boost"] for row in hybrid["results"]}
        return {"rr-seed", "rr-related", "rr-isolated"} <= set(graph_boost) and min(
            graph_boost.get("rr-seed", 0.0), graph_boost.get("rr-related", 0.0)
        ) > 0

    hybrid = _hybrid_until_indexed(
        repository,
        "graphrag related-ranking probeword",
        dimension=settings.embedding_dimension,
        required={"rr-seed", "rr-related", "rr-isolated"},
        ready=_boosts_applied,
    )
    boosts = {row["document_key"]: row["score_components"]["graph_boost"] for row in hybrid["results"]}
    assert {"rr-seed", "rr-related", "rr-isolated"} <= set(boosts)
    assert boosts["rr-seed"] > 0  # linked to rr-related
    assert boosts["rr-related"] > 0  # linked to rr-seed
    assert boosts["rr-isolated"] == 0  # no similarity link and no shared entity
    order = [row["document_key"] for row in hybrid["results"]]
    assert order.index("rr-related") < order.index("rr-isolated")


def _hybrid_until_indexed(repository, query: str, *, dimension: int, required: set[str], ready=None) -> dict:
    # ArangoSearch and the vector index are eventually consistent, so freshly-inserted documents
    # may be absent — or present but with not-yet-stable BM25 scores — on the first query. Retry
    # (bounded) until `ready` holds; by default that is just presence of the `required` documents,
    # but a caller can require a stronger condition (e.g. the graph boost being applied).
    def _present(hybrid: dict) -> bool:
        return required <= {row["document_key"] for row in hybrid["results"]}

    ready = ready or _present
    hybrid = hybrid_search(repository, query, dimension=dimension)
    for _ in range(20):
        if ready(hybrid):
            return hybrid
        time.sleep(0.25)
        hybrid = hybrid_search(repository, query, dimension=dimension)
    return hybrid


@pytest.mark.skipif(not _integration_enabled(), reason="set KB_RUN_INTEGRATION=1 with ArangoDB running")
def test_text_search_matches_title_and_body_after_single_granularity() -> None:
    # GR-6 / audit #14: the body is indexed only via chunks now, so a body match must still surface
    # the document (through its chunk) and a title-only match must still surface it (through the
    # retained documents.title link).
    settings = load_settings()
    repository = KnowledgeRepository(ArangoClient(settings))
    bootstrap_schema(repository.client)

    now = "2026-07-11T00:00:00Z"
    source_key = "vg-src"
    repository.upsert("sources", {"_key": source_key, "type": "test", "display_name": source_key, "created_at": now})
    repository.upsert(
        "documents",
        {
            "_key": "vg-doc",
            "source_key": source_key,
            "canonical_id": "vg-doc",
            "title": "vgtitlemarker overview",  # matches only via the title link
            "text": "a note about vgbodymarker and gardening",  # matches only via the chunk link
            "url": None,
            "created_at": now,
        },
    )
    repository.upsert(
        "chunks",
        {
            "_key": "vg-doc-c0",
            "document_key": "vg-doc",
            "ordinal": 0,
            "text": "a note about vgbodymarker and gardening",
            "embedding": hash_embedding("vgbodymarker", dimension=settings.embedding_dimension),
            "embedding_model": "hash-v1",
        },
    )
    repository.upsert_edge(
        "chunk_of_document",
        {"_key": "vg-cof", "_from": "chunks/vg-doc-c0", "_to": "documents/vg-doc"},
    )
    repository.upsert_edge(
        "document_from_source",
        {
            "_key": "vg-dfs",
            "_from": "documents/vg-doc",
            "_to": f"sources/{source_key}",
            "import_run_key": "vg-run",
            "provenance": {"raw_snapshot_key": "vg-raw", "url": None},
        },
    )

    def _text_has(query: str) -> bool:
        return "vg-doc" in {row["document_key"] for row in text_search(repository, query, source_key=source_key)["results"]}

    # Body term is only in the chunk text; title term is only in the document title.
    assert _until(lambda: _text_has("vgbodymarker"))
    assert _until(lambda: _text_has("vgtitlemarker"))


def _until(predicate) -> bool:
    # ArangoSearch is eventually consistent; retry (bounded) until the freshly-indexed doc appears.
    for _ in range(20):
        if predicate():
            return True
        time.sleep(0.25)
    return predicate()


@pytest.mark.skipif(not _integration_enabled(), reason="set KB_RUN_INTEGRATION=1 with ArangoDB running")
def test_semantic_search_relevance_gate() -> None:
    # Relevance-gated recall: a semantic hit below the floor is dropped. rg-doc's chunk text equals
    # the query, so its cosine is 1.0 — kept at a 0.99 floor, dropped at a 1.01 floor. Source-scoped
    # semantic uses a direct chunk scan (no index-freshness lag), so this is deterministic.
    settings = load_settings()
    repository = KnowledgeRepository(ArangoClient(settings))
    bootstrap_schema(repository.client)

    now = "2026-07-10T00:00:00Z"
    source_key = "rg-src"
    body = "relevancegate marker unique phrase"
    repository.upsert("sources", {"_key": source_key, "type": "test", "display_name": source_key, "created_at": now})
    repository.upsert(
        "documents",
        {
            "_key": "rg-doc",
            "source_key": source_key,
            "canonical_id": "rg-doc",
            "title": "rg",
            "text": body,
            "url": None,
            "created_at": now,
        },
    )
    repository.upsert(
        "chunks",
        {
            "_key": "rg-doc-c0",
            "document_key": "rg-doc",
            "ordinal": 0,
            "text": body,
            "embedding": hash_embedding(body, dimension=settings.embedding_dimension),
            "embedding_model": "hash-v1",
        },
    )

    kept = semantic_search(repository, body, source_key=source_key, dimension=settings.embedding_dimension, min_similarity=0.99)
    assert "rg-doc" in {row["document_key"] for row in kept["results"]}  # cosine ~1.0 clears a 0.99 floor
    dropped = semantic_search(
        repository, body, source_key=source_key, dimension=settings.embedding_dimension, min_similarity=1.01
    )
    assert "rg-doc" not in {row["document_key"] for row in dropped["results"]}  # nothing clears a 1.01 floor


@pytest.mark.skipif(not _integration_enabled(), reason="set KB_RUN_INTEGRATION=1 with ArangoDB running")
def test_reembed_switches_embedding_dimension() -> None:
    # build_embeddings re-embeds every chunk with the configured provider and rebuilds the vector
    # index at its dimension — this is how you switch providers/models after ingest. Runs in its own
    # database so the global re-embed does not disturb other tests.
    base = load_settings()
    settings = cast(Settings, dataclasses.replace(base, arango_database=f"{base.arango_database}_reembed", embedding_dimension=8))
    client = ArangoClient(settings)
    with contextlib.suppress(ArangoError):
        client.request("DELETE", f"/_api/database/{settings.arango_database}", expected=(200, 404))
    repository = KnowledgeRepository(client)
    bootstrap_schema(client, embedding_dimension=settings.embedding_dimension)
    ingest_fixture(repository, settings)

    dims_before = set(repository.client.aql("FOR c IN chunks FILTER HAS(c, 'embedding') RETURN LENGTH(c.embedding)"))
    assert dims_before == {8}

    # A similarity edge from the OLD vector space must be invalidated by re-embedding (PR #30 review).
    repository.upsert_edge(
        "item_related_to_item",
        {"_key": "stale-rel", "_from": "chunks/a", "_to": "chunks/b", "weight": 0.9, "method": "embedding-similarity"},
    )

    new_settings = cast(Settings, dataclasses.replace(settings, embedding_dimension=16))
    result = rebuild_indexes(repository, target="embeddings", embedding_dimension=16, settings=new_settings)
    assert result["status"] == "ok"
    assert result["counts"]["embedding_dimension"] == 16
    assert result["counts"]["chunks_reembedded"] >= 1
    assert result["counts"]["related_edges_removed"] >= 1  # the stale similarity edge was cleared

    dims_after = set(repository.client.aql("FOR c IN chunks FILTER HAS(c, 'embedding') RETURN LENGTH(c.embedding)"))
    assert dims_after == {16}  # every chunk re-embedded at the new dimension
    assert set(repository.client.aql("FOR c IN chunks RETURN c.embedding_model")) == {"hash-v1"}
    # Stale embedding-similarity edges are gone; rebuild them with --target related on the new space.
    remaining = repository.client.aql(
        'RETURN LENGTH(FOR e IN item_related_to_item FILTER e.method == "embedding-similarity" RETURN 1)'
    )
    assert remaining[0] == 0
    # Semantic search serves via the rebuilt 16-dim ANN index — status "ok" proves the index was
    # actually recreated and is serving (a broken/absent index would degrade to full-scan).
    assert semantic_search(repository, "systems thinking", dimension=16)["status"] == "ok"

    with contextlib.suppress(ArangoError):
        client.request("DELETE", f"/_api/database/{settings.arango_database}", expected=(200, 404))


@pytest.mark.skipif(not _integration_enabled(), reason="set KB_RUN_INTEGRATION=1 with ArangoDB running")
def test_reembed_abort_leaves_the_old_embedding_space_intact(monkeypatch: pytest.MonkeyPatch) -> None:
    # Crash-safety: if the completeness gate fails partway (a lost batch / concurrent change), the
    # rebuild must roll back the shadow fields and leave the live embeddings and index untouched,
    # rather than promoting a partial re-embed into a split space with a mismatched/dropped index.
    base = load_settings()
    settings = cast(
        Settings, dataclasses.replace(base, arango_database=f"{base.arango_database}_reembed_abort", embedding_dimension=8)
    )
    client = ArangoClient(settings)
    with contextlib.suppress(ArangoError):
        client.request("DELETE", f"/_api/database/{settings.arango_database}", expected=(200, 404))
    repository = KnowledgeRepository(client)
    bootstrap_schema(client, embedding_dimension=settings.embedding_dimension)
    ingest_fixture(repository, settings)

    # Force the gate to see a corpus larger than what was staged, as a lost batch would.
    real_count = indexing._count_chunks
    monkeypatch.setattr(indexing, "_count_chunks", lambda repo: real_count(repo) + 1)

    new_settings = cast(Settings, dataclasses.replace(settings, embedding_dimension=16))
    with pytest.raises(EmbeddingRebuildError):
        rebuild_indexes(repository, target="embeddings", embedding_dimension=16, settings=new_settings)

    # The old 8-dim space is fully intact: dimension and model unchanged, and no shadow fields leaked.
    assert set(repository.client.aql("FOR c IN chunks FILTER HAS(c, 'embedding') RETURN LENGTH(c.embedding)")) == {8}
    assert set(repository.client.aql("FOR c IN chunks RETURN c.embedding_model")) == {"hash-v1"}
    assert repository.client.aql("RETURN LENGTH(FOR c IN chunks FILTER HAS(c, 'embedding_pending') RETURN 1)")[0] == 0
    # The 8-dim vector index was never dropped (the abort happened before Phase 2). Assert its presence
    # directly rather than via semantic status, which on the tiny fixture depends on ANN recall.
    assert _vector_index_present(client)
    assert semantic_search(repository, "systems thinking", dimension=8)["status"] in {"ok", "degraded"}

    with contextlib.suppress(ArangoError):
        client.request("DELETE", f"/_api/database/{settings.arango_database}", expected=(200, 404))


@pytest.mark.skipif(not _integration_enabled(), reason="set KB_RUN_INTEGRATION=1 with ArangoDB running")
def test_reembed_recovers_after_a_mid_swap_interruption(monkeypatch: pytest.MonkeyPatch) -> None:
    # Crash-safety recovery: interrupt Phase 2 after the index is dropped but before the swap
    # completes (live embeddings still old, shadow fields staged, no index), then re-run and assert
    # the rebuild converges to the new dimension with a serving index and no shadow-field residue.
    base = load_settings()
    settings = cast(
        Settings, dataclasses.replace(base, arango_database=f"{base.arango_database}_reembed_recover", embedding_dimension=8)
    )
    client = ArangoClient(settings)
    with contextlib.suppress(ArangoError):
        client.request("DELETE", f"/_api/database/{settings.arango_database}", expected=(200, 404))
    repository = KnowledgeRepository(client)
    bootstrap_schema(client, embedding_dimension=settings.embedding_dimension)
    ingest_fixture(repository, settings)
    new_settings = cast(Settings, dataclasses.replace(settings, embedding_dimension=16))

    # First run: drop_index has already executed by the time the swap is reached, so raising here
    # leaves the mid-Phase-2 state (index gone, shadow fields present, live embeddings still 8-dim).
    def _crash_during_swap(_repository: KnowledgeRepository) -> int:
        raise RuntimeError("simulated crash during embedding swap")

    monkeypatch.setattr(indexing, "_swap_pending_embeddings", _crash_during_swap)
    with pytest.raises(RuntimeError):
        rebuild_indexes(repository, target="embeddings", embedding_dimension=16, settings=new_settings)
    assert repository.client.aql("RETURN LENGTH(FOR c IN chunks FILTER HAS(c, 'embedding_pending') RETURN 1)")[0] >= 1

    # Recovery: restore the real swap and re-run — the corpus must converge to the new space.
    monkeypatch.undo()
    result = rebuild_indexes(repository, target="embeddings", embedding_dimension=16, settings=new_settings)
    assert result["status"] == "ok"
    assert set(repository.client.aql("FOR c IN chunks FILTER HAS(c, 'embedding') RETURN LENGTH(c.embedding)")) == {16}
    assert repository.client.aql("RETURN LENGTH(FOR c IN chunks FILTER HAS(c, 'embedding_pending') RETURN 1)")[0] == 0
    assert semantic_search(repository, "systems thinking", dimension=16)["status"] == "ok"

    with contextlib.suppress(ArangoError):
        client.request("DELETE", f"/_api/database/{settings.arango_database}", expected=(200, 404))


@pytest.mark.skipif(not _integration_enabled(), reason="set KB_RUN_INTEGRATION=1 with ArangoDB running")
def test_build_communities_clusters_similarity_graph() -> None:
    # GR-4: documents linked by item_related_to_item similarity edges are grouped into a community
    # with an extractive summary (size + shared topics); a document with no similarity edges stays
    # unclustered. Runs in its own database so global community detection cannot see other tests'
    # similarity edges.
    base = load_settings()
    settings = cast(Settings, dataclasses.replace(base, arango_database=f"{base.arango_database}_communities"))
    client = ArangoClient(settings)
    with contextlib.suppress(ArangoError):
        client.request("DELETE", f"/_api/database/{settings.arango_database}", expected=(200, 404))
    repository = KnowledgeRepository(client)
    bootstrap_schema(client, embedding_dimension=settings.embedding_dimension)

    now = "2026-07-10T00:00:00Z"
    repository.upsert("sources", {"_key": "cm-src", "type": "test", "display_name": "cm", "created_at": now})
    repository.upsert("topics", {"_key": "cm-topic", "label": "Systems Thinking", "created_at": now})
    for key in ("cm-a", "cm-b", "cm-c"):
        repository.upsert(
            "documents",
            {"_key": key, "source_key": "cm-src", "canonical_id": key, "title": key, "text": key, "url": None, "created_at": now},
        )
        # Chunks carry an embedding only to satisfy the vector index; community detection reads the
        # similarity edges, not the vectors.
        repository.upsert(
            "chunks",
            {
                "_key": f"{key}-c0",
                "document_key": key,
                "ordinal": 0,
                "text": key,
                "embedding": hash_embedding(key, dimension=settings.embedding_dimension),
                "embedding_model": "hash-v1",
            },
        )
        repository.upsert_edge(
            "chunk_of_document",
            {"_key": f"{key}-cod", "_from": f"chunks/{key}-c0", "_to": f"documents/{key}", "method": "test"},
        )
        repository.upsert_edge(
            "document_mentions_topic",
            {"_key": f"{key}-dmt", "_from": f"documents/{key}", "_to": "topics/cm-topic", "method": "test"},
        )
    # A lonely document with no similarity edges must be left unclustered.
    repository.upsert(
        "documents",
        {
            "_key": "cm-lonely",
            "source_key": "cm-src",
            "canonical_id": "cm-lonely",
            "title": "cm-lonely",
            "text": "x",
            "url": None,
            "created_at": now,
        },
    )
    # A chain a-b-c of similarity edges collapses into a single community {a, b, c}.
    repository.upsert_edge(
        "item_related_to_item",
        {"_key": "cm-ab", "_from": "chunks/cm-a-c0", "_to": "chunks/cm-b-c0", "weight": 0.9, "method": "embedding-similarity"},
    )
    repository.upsert_edge(
        "item_related_to_item",
        {"_key": "cm-bc", "_from": "chunks/cm-b-c0", "_to": "chunks/cm-c-c0", "weight": 0.8, "method": "embedding-similarity"},
    )

    first = build_communities(repository)
    assert first["documents_clustered"] == 3
    assert first["communities"] == 1
    assert first["communities_removed"] == 0  # nothing to clear on the first build

    communities = repository.client.aql("FOR c IN communities RETURN c")
    assert len(communities) == 1
    assert communities[0]["size"] == 3
    assert communities[0]["method"] == "louvain"
    assert "Systems Thinking" in communities[0]["top_topics"]
    assert "Systems Thinking" in communities[0]["summary"]

    members = set(repository.client.aql("FOR e IN document_in_community RETURN e._from"))
    assert members == {"documents/cm-a", "documents/cm-b", "documents/cm-c"}
    assert "documents/cm-lonely" not in members

    # Rebuilding via the CLI target is idempotent: the first build's 3 membership edges are cleared
    # and recreated, leaving exactly one community node (no duplicates).
    second = rebuild_indexes(
        repository, target="communities", embedding_dimension=settings.embedding_dimension, settings=settings
    )
    assert second["counts"]["communities"] == 1
    assert second["counts"]["communities_removed"] == 3
    assert len(repository.client.aql("FOR c IN communities RETURN 1")) == 1

    with contextlib.suppress(ArangoError):
        client.request("DELETE", f"/_api/database/{settings.arango_database}", expected=(200, 404))


@pytest.mark.skipif(not _integration_enabled(), reason="set KB_RUN_INTEGRATION=1 with ArangoDB running")
def test_graphrag_local_and_global_search() -> None:
    # GR-5: global search maps retrieval hits to their communities and ranks communities; local search
    # expands the seed documents into connecting entities, similarity-neighbours, and communities.
    # Retrieval is made deterministic by gating the noisy hash-vector hits (min_similarity=1.01), so
    # hybrid is effectively BM25-only over the crafted corpus. Runs in its own database.
    base = load_settings()
    settings = cast(Settings, dataclasses.replace(base, arango_database=f"{base.arango_database}_graphrag"))
    client = ArangoClient(settings)
    with contextlib.suppress(ArangoError):
        client.request("DELETE", f"/_api/database/{settings.arango_database}", expected=(200, 404))
    repository = KnowledgeRepository(client)
    bootstrap_schema(client, embedding_dimension=settings.embedding_dimension)
    dim = settings.embedding_dimension
    now = "2026-07-11T00:00:00Z"

    repository.upsert("sources", {"_key": "gs-src", "type": "test", "display_name": "gs", "created_at": now})
    repository.upsert("topics", {"_key": "gs-databases", "label": "Databases", "created_at": now})
    repository.upsert("topics", {"_key": "gs-leadership", "label": "Leadership", "created_at": now})

    corpus = {
        "gs-db1": ("Distributed Databases", "distributed database consensus replication quorum", "gs-databases"),
        "gs-db2": ("Consensus Systems", "distributed consensus quorum database replication", "gs-databases"),
        "gs-db3": ("Sharding Guide", "sharding partition tolerance rebalancing shards", "gs-databases"),
        "gs-mg1": ("Engineering Leadership", "engineering leadership coaching growing teams", "gs-leadership"),
        "gs-mg2": ("Team Management", "management leadership coaching engineering teams", "gs-leadership"),
    }
    for key, (title, text, topic) in corpus.items():
        repository.upsert(
            "documents",
            {
                "_key": key,
                "source_key": "gs-src",
                "canonical_id": key,
                "title": title,
                "text": text,
                "url": None,
                "created_at": now,
            },
        )
        repository.upsert(
            "chunks",
            {
                "_key": f"{key}-c0",
                "document_key": key,
                "ordinal": 0,
                "text": text,
                "embedding": hash_embedding(text, dimension=dim),
                "embedding_model": "hash-v1",
            },
        )
        repository.upsert_edge(
            "chunk_of_document",
            {"_key": f"{key}-cod", "_from": f"chunks/{key}-c0", "_to": f"documents/{key}", "method": "test"},
        )
        repository.upsert_edge(
            "document_mentions_topic",
            {"_key": f"{key}-dmt", "_from": f"documents/{key}", "_to": f"topics/{topic}", "method": "test"},
        )

    def relate(a: str, b: str, weight: float) -> None:
        repository.upsert_edge(
            "item_related_to_item",
            {
                "_key": f"{a}-{b}",
                "_from": f"chunks/{a}-c0",
                "_to": f"chunks/{b}-c0",
                "weight": weight,
                "method": "embedding-similarity",
            },
        )

    # A cross-source neighbour of gs-db1: similarity-linked but in a DIFFERENT source, and lexically
    # off both queries — reachable only via graph expansion. Used to prove source scoping.
    repository.upsert("sources", {"_key": "gs-src-other", "type": "test", "display_name": "other", "created_at": now})
    repository.upsert(
        "documents",
        {
            "_key": "gs-x1",
            "source_key": "gs-src-other",
            "canonical_id": "gs-x1",
            "title": "Storage Engine Internals",
            "text": "storage engine internals rocksdb lsm compaction",
            "url": None,
            "created_at": now,
        },
    )
    repository.upsert(
        "chunks",
        {
            "_key": "gs-x1-c0",
            "document_key": "gs-x1",
            "ordinal": 0,
            "text": "storage engine internals rocksdb lsm compaction",
            "embedding": hash_embedding("storage engine internals rocksdb lsm compaction", dimension=dim),
            "embedding_model": "hash-v1",
        },
    )
    repository.upsert_edge(
        "chunk_of_document",
        {"_key": "gs-x1-cod", "_from": "chunks/gs-x1-c0", "_to": "documents/gs-x1", "method": "test"},
    )

    relate("gs-db1", "gs-db2", 0.9)
    relate("gs-db1", "gs-db3", 0.8)  # db3 joins the db cluster but does not match the query lexically
    relate("gs-db1", "gs-x1", 0.7)  # cross-source neighbour, only reachable via graph expansion
    relate("gs-mg1", "gs-mg2", 0.9)
    build_communities(repository)  # -> {db1, db2, db3, x1} and {mg1, mg2}

    query = "distributed database consensus quorum"
    # Wait until ArangoSearch has indexed both clusters (eventual consistency). Poll the BM25 path
    # directly so the readiness signal matches the min_similarity=1.01 (BM25-only) retrieval under
    # test — a hybrid wait could be satisfied early by noisy hash-vector hits before BM25 is ready.
    for _ in range(40):
        db_indexed = {row["document_key"] for row in text_search(repository, query)["results"]}
        both_indexed = {row["document_key"] for row in text_search(repository, "database leadership")["results"]}
        if {"gs-db1", "gs-db2"} <= db_indexed and {"gs-mg1", "gs-mg2"} <= both_indexed:
            break
        time.sleep(0.25)

    # GLOBAL: only the db documents match BM25, so the db community is returned with its summary.
    global_result = global_search(repository, query, dimension=dim, min_similarity=1.01)
    assert global_result["status"] in {"ok", "degraded"}  # vector leg may report ANN fallback while BM25 seeds communities
    assert global_result["communities"], "expected at least one community"
    top = global_result["communities"][0]
    assert "Databases" in top["top_topics"]
    assert top["summary"]
    assert {"gs-db1", "gs-db2"} & {doc["document_key"] for doc in top["documents"]}
    assert all(doc["provenance"] for doc in top["documents"])

    # A query hitting BOTH clusters exercises cross-community ranking (summed score) + truncation.
    both = global_search(repository, "database leadership", dimension=dim, min_similarity=1.01)
    assert len(both["communities"]) == 2
    topics_seen = {topic for community in both["communities"] for topic in community["top_topics"]}
    assert {"Databases", "Leadership"} <= topics_seen
    scores = [community["score"] for community in both["communities"]]
    assert scores == sorted(scores, reverse=True)  # communities ranked by summed relevance
    truncated = global_search(repository, "database leadership", dimension=dim, min_similarity=1.01, community_limit=1)
    assert len(truncated["communities"]) == 1  # community_limit truncates end to end

    # LOCAL: seeds are the db documents; expansion surfaces the topic, the off-query neighbour, community.
    local_result = local_search(repository, query, limit=2, dimension=dim, min_similarity=1.01)
    assert local_result["status"] in {"ok", "degraded"}
    seed_keys = {row["document_key"] for row in local_result["seeds"]}
    assert seed_keys <= {"gs-db1", "gs-db2"}
    assert "Databases" in {entity["label"] for entity in local_result["entities"]}
    related = local_result["related_documents"]
    related_keys = {row["document_key"] for row in related}
    assert "gs-db3" in related_keys  # graph-expanded neighbour that was not itself a query hit
    assert "gs-x1" in related_keys  # unscoped: the cross-source neighbour is included
    assert seed_keys.isdisjoint(related_keys)  # related documents exclude the seeds
    assert any("Databases" in community["top_topics"] for community in local_result["communities"])
    # Every related document carries the full provenance object (PR #32 review), not just source_key+url.
    provenance_fields = {"source_key", "raw_snapshot_key", "import_run_key", "medium_post", "url", "captured_at"}
    assert related and all(provenance_fields <= set(row["provenance"]) for row in related)

    # Source scope must also constrain graph-expanded related documents (PR #32 Codex P2).
    scoped = local_search(repository, query, limit=2, dimension=dim, source_key="gs-src", min_similarity=1.01)
    scoped_related = {row["document_key"] for row in scoped["related_documents"]}
    assert "gs-db3" in scoped_related  # same-source neighbour kept
    assert "gs-x1" not in scoped_related  # cross-source neighbour excluded under source scope

    with contextlib.suppress(ArangoError):
        client.request("DELETE", f"/_api/database/{settings.arango_database}", expected=(200, 404))


@pytest.mark.skipif(not _integration_enabled(), reason="set KB_RUN_INTEGRATION=1 with ArangoDB running")
def test_hybrid_graph_candidate_expansion_fills_empty_slots() -> None:
    # GR-3c: when relevance-gated retrieval leaves empty slots, hybrid pulls in graph-only neighbours
    # of the top hits (item_related_to_item) that had no text/vector hit — appended AFTER the real
    # hits (never outranking them), source-scoped, with full provenance. Deterministic via
    # min_similarity=1.01 (BM25-only), so the neighbours enter only through graph expansion.
    base = load_settings()
    settings = cast(Settings, dataclasses.replace(base, arango_database=f"{base.arango_database}_expand"))
    client = ArangoClient(settings)
    with contextlib.suppress(ArangoError):
        client.request("DELETE", f"/_api/database/{settings.arango_database}", expected=(200, 404))
    repository = KnowledgeRepository(client)
    bootstrap_schema(client, embedding_dimension=settings.embedding_dimension)
    dim = settings.embedding_dimension
    now = "2026-07-11T00:00:00Z"

    repository.upsert("sources", {"_key": "gc-src", "type": "test", "display_name": "gc", "created_at": now})
    repository.upsert("sources", {"_key": "gc-src-2", "type": "test", "display_name": "gc2", "created_at": now})
    corpus = {
        "gc-seed": ("gc-src", "Raft Protocol", "distributed consensus raft protocol election"),
        "gc-related": ("gc-src", "Unrelated Note", "quantum knitting sourdough zzz marker"),
        "gc-other": ("gc-src-2", "Cross Source", "botanical watercolor techniques qqq marker"),
    }
    for key, (src, title, text) in corpus.items():
        repository.upsert(
            "documents",
            {"_key": key, "source_key": src, "canonical_id": key, "title": title, "text": text, "url": None, "created_at": now},
        )
        repository.upsert(
            "chunks",
            {
                "_key": f"{key}-c0",
                "document_key": key,
                "ordinal": 0,
                "text": text,
                "embedding": hash_embedding(text, dimension=dim),
                "embedding_model": "hash-v1",
            },
        )
        repository.upsert_edge(
            "chunk_of_document", {"_key": f"{key}-cod", "_from": f"chunks/{key}-c0", "_to": f"documents/{key}", "method": "test"}
        )

    def relate(a: str, b: str, weight: float) -> None:
        repository.upsert_edge(
            "item_related_to_item",
            {
                "_key": f"{a}-{b}",
                "_from": f"chunks/{a}-c0",
                "_to": f"chunks/{b}-c0",
                "weight": weight,
                "method": "embedding-similarity",
            },
        )

    relate("gc-seed", "gc-related", 0.8)  # same-source neighbour, no lexical/semantic hit
    relate("gc-seed", "gc-other", 0.75)  # cross-source neighbour

    query = "distributed consensus raft protocol"
    for _ in range(40):
        if "gc-seed" in {row["document_key"] for row in text_search(repository, query)["results"]}:
            break
        time.sleep(0.25)

    result = hybrid_search(repository, query, limit=5, dimension=dim, min_similarity=1.01)
    keys = [row["document_key"] for row in result["results"]]
    assert keys[0] == "gc-seed"  # the real hit is ranked first
    expanded = [row for row in result["results"] if row.get("graph_expanded")]
    expanded_keys = {row["document_key"] for row in expanded}
    assert {"gc-related", "gc-other"} <= expanded_keys  # graph-only neighbours filled the empty slots
    # Capped graph-only candidates never outrank the real hit, and the list stays monotonic.
    seed_score = next(row["score"] for row in result["results"] if row["document_key"] == "gc-seed")
    assert all(row["score"] <= seed_score for row in expanded)
    scores = [row["score"] for row in result["results"]]
    assert scores == sorted(scores, reverse=True)
    provenance_fields = {"source_key", "raw_snapshot_key", "import_run_key", "medium_post", "url", "captured_at"}
    assert expanded and all(provenance_fields <= set(row["provenance"]) for row in expanded)

    # Source scope constrains expansion: the cross-source neighbour is excluded under --source.
    scoped = hybrid_search(repository, query, limit=5, dimension=dim, source_key="gc-src", min_similarity=1.01)
    scoped_keys = {row["document_key"] for row in scoped["results"]}
    assert "gc-related" in scoped_keys  # same-source neighbour kept
    assert "gc-other" not in scoped_keys  # cross-source neighbour excluded

    # No empty slots -> no expansion: limit=1 is filled by the real hit alone.
    tight = hybrid_search(repository, query, limit=1, dimension=dim, min_similarity=1.01)
    assert [row["document_key"] for row in tight["results"]] == ["gc-seed"]
    assert not any(row.get("graph_expanded") for row in tight["results"])

    with contextlib.suppress(ArangoError):
        client.request("DELETE", f"/_api/database/{settings.arango_database}", expected=(200, 404))


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
