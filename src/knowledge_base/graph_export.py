from __future__ import annotations

import json
import os
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from typing import Any

from knowledge_base.exporting import _export_zone_warning
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.visualizing import (
    community_rollups,
    document_similarity_edges,
    document_topic_memberships,
    ego_graph,
    topic_cooccurrence,
)
from knowledge_base.viz_builder import (
    assert_visualization_ready,
    safe_public_url,
    visualization_documents,
    visualization_metadata,
)

GRAPH_SCHEMA_VERSION = "1"
_GRAPHML_NS = "http://graphml.graphdrawing.org/xmlns"
_XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
_GRAPHML_SCHEMA = "http://graphml.graphdrawing.org/xmlns/1.0/graphml.xsd"

ET.register_namespace("", _GRAPHML_NS)
ET.register_namespace("xsi", _XSI_NS)

_GRAPH_ATTRIBUTES: tuple[tuple[str, str], ...] = (
    ("schema_version", "string"),
    ("built_at", "string"),
    ("database", "string"),
    ("include_drafts", "boolean"),
    ("topic_min_documents", "int"),
    ("embedding_model", "string"),
    ("counts", "string"),
    ("index_runs", "string"),
    ("warnings", "string"),
    ("ego_document_key", "string"),
)
_NODE_ATTRIBUTES: tuple[tuple[str, str], ...] = (
    ("kind", "string"),
    ("key", "string"),
    ("label", "string"),
    ("title", "string"),
    ("url", "string"),
    ("source_key", "string"),
    ("community", "string"),
    ("published_at", "string"),
    ("topics", "string"),
    ("is_ego_center", "boolean"),
)
_EDGE_ATTRIBUTES: tuple[tuple[str, str], ...] = (
    ("kind", "string"),
    ("weight", "double"),
    ("chunk_pairs", "int"),
    ("document_count", "int"),
)


def serialize_node_link(dataset: dict[str, Any]) -> str:
    """Serialize the deterministic public node-link contract without ASCII escaping."""
    safe_dataset = _xml_safe_value(dataset)
    return json.dumps(safe_dataset, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def build_graph_dataset(
    repository: KnowledgeRepository,
    *,
    include_drafts: bool = False,
    topic_min_documents: int = 2,
    ego_document_key: str | None = None,
    built_at: str | None = None,
) -> dict[str, Any]:
    """Build the public node-link graph without document body text."""
    if topic_min_documents < 1:
        raise ValueError("topic_min_documents must be at least 1")
    documents = visualization_documents(repository, include_drafts=include_drafts)
    document_by_key = {row["document_key"]: row for row in documents}
    memberships = document_topic_memberships(repository, include_drafts=include_drafts)
    communities = community_rollups(repository, include_drafts=include_drafts)
    community_by_document = {
        document: community["community_key"]
        for community in communities
        for document in community["documents"]
        if document in document_by_key
    }

    if ego_document_key is None:
        similarity = document_similarity_edges(repository, top_k=None, include_drafts=include_drafts)
        topic_edges = topic_cooccurrence(
            repository,
            min_documents=topic_min_documents,
            include_drafts=include_drafts,
        )
    else:
        ego = ego_graph(
            repository,
            ego_document_key,
            neighbor_limit=10,
            include_drafts=include_drafts,
        )
        selected = {row["document_key"] for row in ego["documents"]}
        document_by_key = {key: row for key, row in document_by_key.items() if key in selected}
        shared_topics = {entity["entity_key"] for entity in ego["entities"] if entity.get("entity_type") == "topic"}
        memberships = [row for row in memberships if row["document_key"] in selected and row["topic_key"] in shared_topics]
        similarity = [edge for edge in ego["similarity_edges"] if edge["source"] in selected and edge["target"] in selected]
        topic_edges = _cooccurrence_from_memberships(memberships, min_documents=topic_min_documents)

    topic_labels = {row["topic_key"]: row["topic_label"] for row in memberships}
    topics_by_document: dict[str, list[str]] = defaultdict(list)
    for membership in memberships:
        if membership["document_key"] in document_by_key:
            topics_by_document[membership["document_key"]].append(membership["topic_key"])

    nodes: list[dict[str, Any]] = []
    for key, row in sorted(document_by_key.items()):
        topics = sorted(set(topics_by_document.get(key, [])))
        node = {
            "id": f"document:{key}",
            "kind": "document",
            "key": key,
            "label": row.get("title") or key,
            "title": row.get("title") or key,
            "url": safe_public_url(row.get("url")),
            "source_key": row.get("source_key"),
            "community": community_by_document.get(key),
            "published_at": row.get("published_at"),
            "topics": topics,
        }
        if ego_document_key is not None:
            node["is_ego_center"] = key == ego_document_key
        nodes.append(node)
    topic_keys = sorted({row["topic_key"] for row in memberships if row["document_key"] in document_by_key})
    for key in topic_keys:
        nodes.append(
            {
                "id": f"topic:{key}",
                "kind": "topic",
                "key": key,
                "label": topic_labels.get(key, key),
            }
        )

    links: list[dict[str, Any]] = [
        {
            "source": f"document:{edge['source']}",
            "target": f"document:{edge['target']}",
            "kind": "document_similarity",
            "weight": round(float(edge["weight"]), 3),
            "chunk_pairs": int(edge["chunk_pairs"]),
        }
        for edge in similarity
        if edge["source"] in document_by_key and edge["target"] in document_by_key
    ]
    links.extend(
        {
            "source": f"topic:{edge['source']}",
            "target": f"topic:{edge['target']}",
            "kind": "topic_cooccurrence",
            "document_count": int(edge["document_count"]),
        }
        for edge in topic_edges
        if edge["source"] in topic_keys and edge["target"] in topic_keys
    )
    links.extend(
        {
            "source": f"document:{membership['document_key']}",
            "target": f"topic:{membership['topic_key']}",
            "kind": "document_topic",
        }
        for membership in memberships
        if membership["document_key"] in document_by_key and membership["topic_key"] in topic_keys
    )
    links.sort(key=lambda row: (row["kind"], row["source"], row["target"]))

    metadata = visualization_metadata(
        repository,
        include_drafts=include_drafts,
        built_at=built_at or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        selected_documents=len(document_by_key),
        selected_topics=len(topic_keys),
        selected_communities=len({community_by_document[key] for key in document_by_key if key in community_by_document}),
        selected_similarity_edges=sum(link["kind"] == "document_similarity" for link in links),
        isolated_documents=sum(key not in community_by_document for key in document_by_key),
        warn_empty_similarity=ego_document_key is None,
    )
    metadata["topic_min_documents"] = topic_min_documents
    metadata["ego_document_key"] = ego_document_key
    metadata["thresholds"] = {
        "similarity_top_k": 10 if ego_document_key is not None else None,
        "topic_cooccurrence_min_documents": topic_min_documents,
    }
    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "directed": False,
        "multigraph": False,
        "meta": metadata,
        "nodes": nodes,
        "links": links,
    }


def export_graph(
    repository: KnowledgeRepository,
    output: Path,
    *,
    output_format: str,
    include_drafts: bool = False,
    topic_min_documents: int = 2,
    ego_document_key: str | None = None,
) -> dict[str, Any]:
    assert_visualization_ready(repository)
    dataset = build_graph_dataset(
        repository,
        include_drafts=include_drafts,
        topic_min_documents=topic_min_documents,
        ego_document_key=ego_document_key,
    )
    result = write_graph_export(dataset, output, output_format=output_format)
    consistency_warnings = dataset["meta"]["warnings"]
    if consistency_warnings:
        result["status"] = "degraded"
        result["warnings"] = consistency_warnings
    return result


def serialize_graphml(dataset: dict[str, Any]) -> str:
    """Serialize the graph dataset as typed, Unicode-safe GraphML."""
    dataset = _xml_safe_value(dataset)
    root = ET.Element(
        _q("graphml"),
        {_q_xsi("schemaLocation"): f"{_GRAPHML_NS} {_GRAPHML_SCHEMA}"},
    )
    for scope, attributes in (
        ("graph", _GRAPH_ATTRIBUTES),
        ("node", _NODE_ATTRIBUTES),
        ("edge", _EDGE_ATTRIBUTES),
    ):
        prefix = scope[0]
        for name, value_type in attributes:
            ET.SubElement(
                root,
                _q("key"),
                {
                    "id": f"{prefix}_{name}",
                    "for": scope,
                    "attr.name": name,
                    "attr.type": value_type,
                },
            )

    graph = ET.SubElement(root, _q("graph"), {"id": "knowledge-base", "edgedefault": "undirected"})
    meta = dataset.get("meta", {})
    graph_values = {
        "schema_version": dataset.get("schema_version", GRAPH_SCHEMA_VERSION),
        "built_at": meta.get("built_at"),
        "database": meta.get("database"),
        "include_drafts": meta.get("include_drafts"),
        "topic_min_documents": meta.get("topic_min_documents"),
        "embedding_model": meta.get("embedding_model"),
        "counts": meta.get("counts"),
        "index_runs": meta.get("index_runs"),
        "warnings": meta.get("warnings"),
        "ego_document_key": meta.get("ego_document_key"),
    }
    _append_data(graph, "g", graph_values, dict(_GRAPH_ATTRIBUTES))

    for node in sorted(dataset.get("nodes", []), key=lambda item: str(item["id"])):
        element = ET.SubElement(graph, _q("node"), {"id": str(node["id"])})
        _append_data(element, "n", node, dict(_NODE_ATTRIBUTES))

    edge_order = sorted(
        dataset.get("links", []),
        key=lambda item: (str(item.get("kind", "")), str(item["source"]), str(item["target"])),
    )
    for ordinal, edge in enumerate(edge_order):
        element = ET.SubElement(
            graph,
            _q("edge"),
            {
                "id": f"e{ordinal}",
                "source": str(edge["source"]),
                "target": str(edge["target"]),
            },
        )
        _append_data(element, "e", edge, dict(_EDGE_ATTRIBUTES))

    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def write_graph_export(dataset: dict[str, Any], output: Path, *, output_format: str) -> dict[str, Any]:
    if output_format not in {"json", "graphml"}:
        raise ValueError(f"Unsupported graph export format: {output_format}")
    warning = _export_zone_warning(output, content="document titles, URLs, topics and knowledge-graph topology")
    serialized = serialize_node_link(dataset) if output_format == "json" else serialize_graphml(dataset)
    payload = serialized.encode("utf-8")
    _atomic_write(output, payload)
    result: dict[str, Any] = {
        "status": "ok",
        "format": output_format,
        "output": str(output),
        "nodes": len(dataset.get("nodes", [])),
        "edges": len(dataset.get("links", [])),
        "bytes": len(payload),
    }
    if warning:
        result["warning"] = warning
    return result


def _append_data(
    parent: ET.Element,
    prefix: str,
    values: dict[str, Any],
    attribute_types: dict[str, str],
) -> None:
    for name in attribute_types:
        value = values.get(name)
        if value is None:
            continue
        element = ET.SubElement(parent, _q("data"), {"key": f"{prefix}_{name}"})
        if isinstance(value, (list, dict)):
            element.text = _xml_safe_text(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
        elif isinstance(value, bool):
            element.text = "true" if value else "false"
        else:
            element.text = _xml_safe_text(str(value))


def _atomic_write(output: Path, payload: bytes) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _cooccurrence_from_memberships(
    memberships: list[dict[str, Any]],
    *,
    min_documents: int,
) -> list[dict[str, Any]]:
    topics_by_document: dict[str, set[str]] = defaultdict(set)
    labels: dict[str, str] = {}
    for row in memberships:
        topics_by_document[row["document_key"]].add(row["topic_key"])
        labels[row["topic_key"]] = row["topic_label"]
    counts: Counter[tuple[str, str]] = Counter()
    for topics in topics_by_document.values():
        counts.update(combinations(sorted(topics), 2))
    return [
        {
            "source": source,
            "target": target,
            "source_label": labels.get(source, source),
            "target_label": labels.get(target, target),
            "document_count": count,
        }
        for (source, target), count in sorted(counts.items())
        if count >= min_documents
    ]


def _q(local: str) -> str:
    return f"{{{_GRAPHML_NS}}}{local}"


def _q_xsi(local: str) -> str:
    return f"{{{_XSI_NS}}}{local}"


def _xml_safe_text(value: str) -> str:
    """Replace characters forbidden by XML 1.0 while preserving Unicode text."""
    return "".join(
        character
        if character in {"\t", "\n", "\r"}
        or "\u0020" <= character <= "\ud7ff"
        or "\ue000" <= character <= "\ufffd"
        or "\U00010000" <= character <= "\U0010ffff"
        else "\ufffd"
        for character in value
    )


def _xml_safe_value(value: Any) -> Any:
    if isinstance(value, str):
        return _xml_safe_text(value)
    if isinstance(value, list):
        return [_xml_safe_value(item) for item in value]
    if isinstance(value, dict):
        return {_xml_safe_text(str(key)): _xml_safe_value(item) for key, item in value.items()}
    return value
