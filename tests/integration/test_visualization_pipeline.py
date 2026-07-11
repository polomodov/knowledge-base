import contextlib
import dataclasses
import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import cast

import pytest

from knowledge_base.arango import ArangoClient, ArangoError
from knowledge_base.config import Settings, load_settings
from knowledge_base.embeddings import hash_embedding
from knowledge_base.graph_export import export_graph
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.schema import bootstrap_schema
from knowledge_base.viz_builder import build_visualization, build_visualization_payload

pytestmark = pytest.mark.integration


def _integration_enabled() -> bool:
    return os.getenv("KB_RUN_INTEGRATION") == "1"


@pytest.mark.skipif(not _integration_enabled(), reason="set KB_RUN_INTEGRATION=1 with ArangoDB running")
def test_visualization_build_and_graph_exports_on_seeded_corpus(tmp_path: Path) -> None:
    repository, client, settings = _fresh_repository("visualization")
    try:
        _seed_visualization_corpus(repository, settings)

        first = build_visualization_payload(repository, timeline_top_topics=3, built_at="2026-07-11T12:00:00Z")
        second = build_visualization_payload(repository, timeline_top_topics=3, built_at="2026-07-11T12:00:00Z")
        assert second == first
        assert [row["key"] for row in first["documents"]] == ["viz-d1", "viz-d2", "viz-d3", "viz-d4"]
        assert len(first["similarity_edges"]) == 3
        assert first["meta"]["isolated_documents"] == 1
        assert first["timeline"]["docs_without_dates"] == 1
        topic_one = next(row for row in first["topics"] if row["key"] == "viz-t1")
        assert topic_one["documents"] == 2  # doc+chunk mentions of d1 were canonically deduplicated
        assert first["meta"]["warnings"] == []
        assert "SecretDraftTopic" not in json.dumps(first, ensure_ascii=False)

        html_path = tmp_path / "knowledge-base.html"
        html_result = build_visualization(repository, html_path, timeline_top_topics=3)
        assert html_result["status"] == "ok"
        assert html_result["documents"] == 4
        assert html_result["communities"] == 2
        assert html_result["status_counts"] == [{"status": "published", "documents": 4}]
        assert html_result["bytes"] == len(html_path.read_bytes())
        html = html_path.read_text(encoding="utf-8")
        assert 'id="map-canvas"' in html
        assert 'id="timeline-svg"' in html
        assert 'id="ego-svg"' in html
        embedded = re.search(r'<script type="application/json" id="kb-data">(.*?)</script>', html, re.DOTALL)
        assert embedded is not None
        assert len(json.loads(embedded.group(1))["documents"]) == 4
        assert "Private draft" not in html
        assert "SecretDraftTopic" not in html
        assert html_path.stat().st_size < 5_000_000

        draft_html_path = tmp_path / "knowledge-base-with-drafts.html"
        draft_html_result = build_visualization(
            repository,
            draft_html_path,
            timeline_top_topics=3,
            include_drafts=True,
        )
        assert draft_html_result["status"] == "ok"
        assert draft_html_result["documents"] == 5
        assert draft_html_result["communities"] == 3
        assert {row["status"] for row in draft_html_result["status_counts"]} == {"published", "draft"}
        draft_html = draft_html_path.read_text(encoding="utf-8")
        assert "Private draft" in draft_html
        assert "SecretDraftTopic" in draft_html

        json_path = tmp_path / "graph.json"
        graphml_path = tmp_path / "graph.graphml"
        json_result = export_graph(repository, json_path, output_format="json")
        graphml_result = export_graph(repository, graphml_path, output_format="graphml")
        assert json_result["status"] == graphml_result["status"] == "ok"
        node_link = json.loads(json_path.read_text(encoding="utf-8"))
        graphml = ET.parse(graphml_path).getroot()
        namespace = {"g": "http://graphml.graphdrawing.org/xmlns"}
        assert len(graphml.findall(".//g:node", namespace)) == len(node_link["nodes"])
        assert len(graphml.findall(".//g:edge", namespace)) == len(node_link["links"])
        assert all("text" not in node for node in node_link["nodes"])
        assert {node["key"] for node in node_link["nodes"] if node["kind"] == "document"} == {
            "viz-d1",
            "viz-d2",
            "viz-d3",
            "viz-d4",
        }

        draft_graph = tmp_path / "graph-with-drafts.json"
        export_graph(repository, draft_graph, output_format="json", include_drafts=True)
        draft_payload = json.loads(draft_graph.read_text(encoding="utf-8"))
        assert "viz-draft" in {node["key"] for node in draft_payload["nodes"]}
        assert any(node.get("community") == "viz-c3" for node in draft_payload["nodes"])

        ego_path = tmp_path / "ego.json"
        export_graph(repository, ego_path, output_format="json", ego_document_key="viz-d1")
        ego_payload = json.loads(ego_path.read_text(encoding="utf-8"))
        assert ego_payload["meta"]["ego_document_key"] == "viz-d1"
        assert 1 < len([node for node in ego_payload["nodes"] if node["kind"] == "document"]) <= 11
    finally:
        _drop_database(client, settings.arango_database)


@pytest.mark.skipif(not _integration_enabled(), reason="set KB_RUN_INTEGRATION=1 with ArangoDB running")
def test_visualization_builds_valid_no_data_html(tmp_path: Path) -> None:
    repository, client, settings = _fresh_repository("visualization_empty")
    try:
        output = tmp_path / "empty.html"
        result = build_visualization(repository, output)
        assert result["status"] == "ok"
        assert result["documents"] == 0
        html = output.read_text(encoding="utf-8")
        match = re.search(r'<script type="application/json" id="kb-data">(.*?)</script>', html, re.DOTALL)
        assert match is not None
        payload = json.loads(match.group(1))
        assert payload["documents"] == []
        assert payload["similarity_edges"] == []

        now = "2026-07-11T00:00:00Z"
        repository.upsert("sources", {"_key": "empty-source", "type": "test", "display_name": "Empty", "created_at": now})
        repository.upsert(
            "documents",
            {
                "_key": "embedded-without-related",
                "source_key": "empty-source",
                "canonical_id": "embedded-without-related",
                "title": "Embedded but disconnected",
                "text": "body",
                "url": None,
                "published_at": now,
                "status": "published",
                "created_at": now,
            },
        )
        repository.upsert(
            "chunks",
            {
                "_key": "embedded-without-related-c0",
                "document_key": "embedded-without-related",
                "ordinal": 0,
                "text": "body",
                "embedding": hash_embedding("body", dimension=settings.embedding_dimension),
                "embedding_model": "hash-v1",
            },
        )
        repository.upsert(
            "documents",
            {
                "_key": "embedded-without-related-2",
                "source_key": "empty-source",
                "canonical_id": "embedded-without-related-2",
                "title": "Second disconnected document",
                "text": "body two",
                "url": None,
                "published_at": now,
                "status": "published",
                "created_at": now,
            },
        )
        repository.upsert(
            "chunks",
            {
                "_key": "embedded-without-related-2-c0",
                "document_key": "embedded-without-related-2",
                "ordinal": 0,
                "text": "body two",
                "embedding": hash_embedding("body two", dimension=settings.embedding_dimension),
                "embedding_model": "hash-v1",
            },
        )
        degraded = build_visualization(repository, tmp_path / "without-related.html")
        assert degraded["status"] == "degraded"
        assert [warning["code"] for warning in degraded["warnings"]] == ["related_index_empty"]
    finally:
        _drop_database(client, settings.arango_database)


def _fresh_repository(suffix: str) -> tuple[KnowledgeRepository, ArangoClient, Settings]:
    base = load_settings()
    settings = cast(Settings, dataclasses.replace(base, arango_database=f"{base.arango_database}_{suffix}"))
    client = ArangoClient(settings)
    _drop_database(client, settings.arango_database)
    repository = KnowledgeRepository(client)
    bootstrap_schema(client, embedding_dimension=settings.embedding_dimension)
    return repository, client, settings


def _drop_database(client: ArangoClient, database: str) -> None:
    with contextlib.suppress(ArangoError):
        client.request("DELETE", f"/_api/database/{database}", expected=(200, 404))


def _seed_visualization_corpus(repository: KnowledgeRepository, settings: Settings) -> None:
    now = "2026-07-11T00:00:00Z"
    for key, label in (("viz-s1", "Source One"), ("viz-s2", "Source Two")):
        repository.upsert("sources", {"_key": key, "type": "test", "display_name": label, "created_at": now})
    for key, label in (("viz-t1", "Architecture"), ("viz-t2", "Systems"), ("viz-t3", "Leadership")):
        repository.upsert("topics", {"_key": key, "label": label, "created_at": now})

    corpus = {
        "viz-d1": ("Первый 🚀 & </script>", "viz-s1", "2026-01-10T00:00:00Z", "published"),
        "viz-d2": ("Second", "viz-s1", "2026-01-20T00:00:00Z", "published"),
        "viz-d3": ("Third", "viz-s2", "2026-02-03T00:00:00Z", "published"),
        "viz-d4": ("No date", "viz-s2", None, "published"),
        "viz-draft": ("Private draft", "viz-s2", "2026-02-05T00:00:00Z", "draft"),
    }
    for key, (title, source, published_at, status) in corpus.items():
        repository.upsert(
            "documents",
            {
                "_key": key,
                "source_key": source,
                "canonical_id": key,
                "title": title,
                "text": f"Body for {key}",
                "url": f"https://example.test/{key}",
                "published_at": published_at,
                "status": status,
                "created_at": now,
            },
        )
        repository.upsert(
            "chunks",
            {
                "_key": f"{key}-c0",
                "document_key": key,
                "ordinal": 0,
                "text": f"Chunk for {key}",
                "embedding": hash_embedding(key, dimension=settings.embedding_dimension),
                "embedding_model": "hash-v1",
            },
        )
        repository.upsert_edge(
            "chunk_of_document",
            {"_key": f"{key}-chunk", "_from": f"chunks/{key}-c0", "_to": f"documents/{key}", "method": "test"},
        )

    def mention(document: str, topic: str, *, chunk: bool = False, suffix: str = "") -> None:
        endpoint = f"chunks/{document}-c0" if chunk else f"documents/{document}"
        repository.upsert_edge(
            "document_mentions_topic",
            {"_key": f"{document}-{topic}-{int(chunk)}{suffix}", "_from": endpoint, "_to": f"topics/{topic}", "method": "test"},
        )

    mention("viz-d1", "viz-t1")
    mention("viz-d1", "viz-t1", chunk=True)  # duplicate canonical membership
    mention("viz-d1", "viz-t2")
    mention("viz-d2", "viz-t1")
    mention("viz-d2", "viz-t2")
    mention("viz-d3", "viz-t2")
    mention("viz-d3", "viz-t3")
    mention("viz-d4", "viz-t3")
    mention("viz-draft", "viz-t1")

    def relate(left: str, right: str, weight: float, suffix: str = "") -> None:
        repository.upsert_edge(
            "item_related_to_item",
            {
                "_key": f"{left}-{right}{suffix}",
                "_from": f"chunks/{left}-c0",
                "_to": f"chunks/{right}-c0",
                "weight": weight,
                "method": "embedding-similarity",
            },
        )

    relate("viz-d1", "viz-d2", 0.9)
    relate("viz-d2", "viz-d1", 0.8, "-reverse")
    relate("viz-d2", "viz-d3", 0.75)
    relate("viz-d3", "viz-d4", 0.7)
    relate("viz-d1", "viz-draft", 0.95)

    communities = {
        "viz-c1": (["viz-d1", "viz-d2"], ["Architecture", "Systems"]),
        "viz-c2": (["viz-d3"], ["Leadership"]),
        "viz-c3": (["viz-d4", "viz-draft"], ["SecretDraftTopic"]),
    }
    for key, (members, top_topics) in communities.items():
        repository.upsert(
            "communities",
            {
                "_key": key,
                "size": len(members),
                "method": "test",
                "top_topics": top_topics,
                "summary": f"{len(members)} documents",
                "created_at": now,
            },
        )
        for member in members:
            repository.upsert_edge(
                "document_in_community",
                {
                    "_key": f"{member}-{key}",
                    "_from": f"documents/{member}",
                    "_to": f"communities/{key}",
                    "method": "test",
                },
            )

    for target, started, finished in (
        ("embeddings", "2026-07-11T09:00:00Z", "2026-07-11T09:01:00Z"),
        ("related", "2026-07-11T09:02:00Z", "2026-07-11T09:03:00Z"),
        ("communities", "2026-07-11T09:04:00Z", "2026-07-11T09:05:00Z"),
    ):
        repository.upsert(
            "index_runs",
            {
                "_key": f"viz-{target}",
                "target": target,
                "status": "ok",
                "started_at": started,
                "finished_at": finished,
                "counts": {},
                "error": None,
            },
        )
