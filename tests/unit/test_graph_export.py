import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import knowledge_base.graph_export as graph_export
from knowledge_base.graph_export import build_graph_dataset, serialize_graphml, serialize_node_link, write_graph_export


def _dataset() -> dict:
    return {
        "schema_version": "1",
        "directed": False,
        "multigraph": False,
        "meta": {
            "built_at": "2026-07-11T12:00:00Z",
            "database": "knowledge_base",
            "include_drafts": False,
            "topic_min_documents": 2,
            "ego_document_key": "doc-1",
        },
        "nodes": [
            {
                "id": "document:doc-1",
                "kind": "document",
                "key": "doc-1",
                "label": 'Привет 🚀 & "Graph"',
                "title": 'Привет 🚀 & "Graph"',
                "source_key": "source-1",
                "community": "community-1",
                "published_at": "2026-07-01T00:00:00Z",
                "topics": ["Графы"],
                "is_ego_center": True,
            },
            {"id": "topic:graphs", "kind": "topic", "key": "graphs", "label": "Графы"},
        ],
        "links": [
            {
                "source": "document:doc-1",
                "target": "document:doc-2",
                "kind": "document_similarity",
                "weight": 0.875,
                "chunk_pairs": 3,
            },
            {
                "source": "topic:graphs",
                "target": "topic:systems",
                "kind": "topic_cooccurrence",
                "document_count": 4,
            },
        ],
    }


def test_node_link_round_trip_preserves_unicode() -> None:
    payload = serialize_node_link(_dataset())
    parsed = json.loads(payload)
    assert parsed["nodes"][0]["title"] == 'Привет 🚀 & "Graph"'
    assert "\\u041f" not in payload


def test_graphml_is_typed_and_xml_safe() -> None:
    payload = serialize_graphml(_dataset())
    root = ET.fromstring(payload)
    namespace = {"g": "http://graphml.graphdrawing.org/xmlns"}
    assert root.find("g:graph", namespace) is not None
    keys = {element.attrib["attr.name"]: element.attrib["attr.type"] for element in root.findall("g:key", namespace)}
    assert keys["weight"] == "double"
    assert keys["chunk_pairs"] == "int"
    assert keys["is_ego_center"] == "boolean"
    assert "Привет 🚀 &amp;" in payload
    assert "ego_document_key" in payload


def test_graphml_replaces_xml_1_control_characters() -> None:
    dataset = _dataset()
    dataset["nodes"][0]["title"] = "before\x01after"
    payload = serialize_graphml(dataset)
    ET.fromstring(payload)
    assert "\x01" not in payload
    assert "before�after" in payload
    node_link = json.loads(serialize_node_link(dataset))
    assert node_link["nodes"][0]["title"] == "before�after"


def test_graphml_and_node_link_round_trip_the_same_node_and_edge_content() -> None:
    node_link = json.loads(serialize_node_link(_dataset()))
    root = ET.fromstring(serialize_graphml(_dataset()))
    namespace = {"g": "http://graphml.graphdrawing.org/xmlns"}
    keys = {
        element.attrib["id"]: (element.attrib["attr.name"], element.attrib["attr.type"])
        for element in root.findall("g:key", namespace)
    }

    graphml_nodes = []
    for element in root.findall(".//g:node", namespace):
        row = {"id": element.attrib["id"]}
        for data in element.findall("g:data", namespace):
            name, value_type = keys[data.attrib["key"]]
            row[name] = _graphml_value(data.text or "", value_type, name=name)
        graphml_nodes.append(row)
    expected_nodes = [{key: value for key, value in node.items() if value is not None} for node in node_link["nodes"]]
    assert sorted(graphml_nodes, key=lambda row: row["id"]) == sorted(expected_nodes, key=lambda row: row["id"])

    graphml_edges = []
    for element in root.findall(".//g:edge", namespace):
        row = {"source": element.attrib["source"], "target": element.attrib["target"]}
        for data in element.findall("g:data", namespace):
            name, value_type = keys[data.attrib["key"]]
            row[name] = _graphml_value(data.text or "", value_type, name=name)
        graphml_edges.append(row)
    expected_edges = [{key: value for key, value in edge.items() if value is not None} for edge in node_link["links"]]

    def edge_key(row):
        return row["kind"], row["source"], row["target"]

    assert sorted(graphml_edges, key=edge_key) == sorted(expected_edges, key=edge_key)


def test_write_graph_export_is_atomic_and_reports_bytes(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "graph.json"
    result = write_graph_export(_dataset(), output, output_format="json")
    assert result["status"] == "ok"
    assert result["nodes"] == 2
    assert result["edges"] == 2
    assert result["bytes"] == len(output.read_bytes())
    assert result["warning"] == "output_outside_generated_zone"
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == "1"
    assert not list(output.parent.glob(".*.tmp"))


def test_write_graph_export_rejects_unknown_format(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported graph export format"):
        write_graph_export(_dataset(), tmp_path / "graph.txt", output_format="dot")


def test_build_graph_dataset_combines_documents_topics_and_communities(monkeypatch) -> None:
    documents = [
        {
            "document_key": "d1",
            "title": "One",
            "url": "https://example.test/1",
            "source_key": "source",
            "published_at": "2026-01-01T00:00:00Z",
            "status": "published",
        },
        {
            "document_key": "d2",
            "title": "Two",
            "url": "javascript:alert(1)",
            "source_key": "source",
            "published_at": "2026-01-02T00:00:00Z",
            "status": "published",
        },
    ]
    memberships = [
        {
            "document_key": key,
            "topic_key": "t1",
            "topic_label": "Topic",
            "source_key": "source",
            "published_at": "2026-01-01T00:00:00Z",
            "status": "published",
        }
        for key in ("d1", "d2")
    ]
    monkeypatch.setattr(graph_export, "_visualization_documents", lambda repository, **kwargs: documents)
    monkeypatch.setattr(graph_export, "document_topic_memberships", lambda repository, **kwargs: memberships)
    monkeypatch.setattr(
        graph_export,
        "document_similarity_edges",
        lambda repository, **kwargs: [{"source": "d1", "target": "d2", "weight": 0.9, "chunk_pairs": 2}],
    )
    monkeypatch.setattr(
        graph_export,
        "topic_cooccurrence",
        lambda repository, **kwargs: [
            {
                "source": "t1",
                "target": "t2",
                "source_label": "Topic",
                "target_label": "Second",
                "document_count": 2,
            }
        ],
    )
    monkeypatch.setattr(
        graph_export,
        "community_rollups",
        lambda repository, **kwargs: [
            {
                "community_key": "c1",
                "label": "Community",
                "size": 2,
                "summary": "summary",
                "top_topics": ["Topic"],
                "documents": ["d1", "d2"],
            }
        ],
    )
    monkeypatch.setattr(
        graph_export,
        "_visualization_metadata",
        lambda repository, **kwargs: {
            "built_at": kwargs["built_at"],
            "database": "test",
            "embedding_model": "hash-v1",
            "counts": {},
            "index_runs": {},
            "warnings": [],
        },
    )

    dataset = build_graph_dataset(  # type: ignore[arg-type]
        object(),
        built_at="2026-07-11T12:00:00Z",
        topic_min_documents=3,
    )

    document_nodes = [node for node in dataset["nodes"] if node["kind"] == "document"]
    assert [node["key"] for node in document_nodes] == ["d1", "d2"]
    assert document_nodes[0]["community"] == "c1"
    assert document_nodes[1]["url"] is None
    assert all("text" not in node for node in dataset["nodes"])
    assert {link["kind"] for link in dataset["links"]} == {"document_similarity", "document_topic"}
    assert dataset["meta"]["topic_min_documents"] == 3
    assert dataset["meta"]["thresholds"] == {
        "similarity_top_k": None,
        "topic_cooccurrence_min_documents": 3,
    }


def test_ego_export_keeps_only_topics_shared_with_center(monkeypatch) -> None:
    documents = [
        {
            "document_key": key,
            "title": key,
            "url": None,
            "source_key": "source",
            "published_at": "2026-01-01T00:00:00Z",
            "status": "published",
        }
        for key in ("center", "neighbor")
    ]
    memberships = [
        {"document_key": "center", "topic_key": "shared", "topic_label": "Shared"},
        {"document_key": "center", "topic_key": "center-only", "topic_label": "Center only"},
        {"document_key": "neighbor", "topic_key": "shared", "topic_label": "Shared"},
        {"document_key": "neighbor", "topic_key": "neighbor-only", "topic_label": "Neighbor only"},
    ]
    monkeypatch.setattr(graph_export, "_visualization_documents", lambda repository, **kwargs: documents)
    monkeypatch.setattr(graph_export, "document_topic_memberships", lambda repository, **kwargs: memberships)
    monkeypatch.setattr(graph_export, "community_rollups", lambda repository, **kwargs: [])
    monkeypatch.setattr(
        graph_export,
        "ego_graph",
        lambda repository, document_key, **kwargs: {
            "center": documents[0],
            "documents": documents,
            "similarity_edges": [{"source": "center", "target": "neighbor", "weight": 0.9, "chunk_pairs": 1}],
            "entities": [
                {
                    "id": "topic:shared",
                    "entity_type": "topic",
                    "entity_key": "shared",
                    "label": "Shared",
                    "document_keys": ["center", "neighbor"],
                }
            ],
            "entity_edges": [],
        },
    )
    monkeypatch.setattr(
        graph_export,
        "_visualization_metadata",
        lambda repository, **kwargs: {
            "built_at": kwargs["built_at"],
            "database": "test",
            "embedding_model": "hash-v1",
            "counts": {},
            "index_runs": {},
            "warnings": [],
        },
    )

    dataset = build_graph_dataset(object(), ego_document_key="center")  # type: ignore[arg-type]

    assert {node["key"] for node in dataset["nodes"] if node["kind"] == "topic"} == {"shared"}
    document_topic = [link for link in dataset["links"] if link["kind"] == "document_topic"]
    assert {(link["source"], link["target"]) for link in document_topic} == {
        ("document:center", "topic:shared"),
        ("document:neighbor", "topic:shared"),
    }
    assert next(node for node in dataset["nodes"] if node["key"] == "center")["is_ego_center"] is True


def _graphml_value(value: str, value_type: str, *, name: str):
    if value_type == "boolean":
        return value == "true"
    if value_type == "int":
        return int(value)
    if value_type == "double":
        return float(value)
    if name == "topics":
        return json.loads(value)
    return value
