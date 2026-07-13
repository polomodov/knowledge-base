from __future__ import annotations

import json
import math
import os
import tempfile
from collections import Counter, defaultdict
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from knowledge_base.config import REPO_ROOT
from knowledge_base.constants import RELATED_EDGE_METHOD
from knowledge_base.exporting import _export_zone_warning
from knowledge_base.freshness import derived_index_stale_messages
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.schema import health_report
from knowledge_base.visualizing import (
    community_rollups,
    document_similarity_projection,
    document_topic_memberships,
    timeline_buckets,
    topic_cooccurrence,
)
from knowledge_base.viz_layouts import UNCLUSTERED_ZONE, fruchterman_reingold, phyllotaxis_layout

VIZ_SCHEMA_VERSION = "1"
DEFAULT_VIZ_OUTPUT = REPO_ROOT / "data" / "generated" / "viz" / "knowledge-base.html"
MAX_VIZ_BYTES = 5_000_000
_DATA_MARKER = "__KB_DATA__"
_MAP_WIDTH = 1800.0
_MAP_HEIGHT = 1100.0


def load_visualization_template() -> str:
    template = resources.files("knowledge_base").joinpath("templates", "viz_template.html")
    return template.read_text(encoding="utf-8")


def serialize_embedded_json(payload: dict[str, Any]) -> str:
    """Return compact JSON that cannot terminate its application/json script element."""
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    # Escaping the slash is valid JSON and prevents any case-sensitive closing tag beginning with
    # `</`. Escaping the opening angle bracket in an HTML comment keeps legacy parser states inert.
    return serialized.replace("</", "<\\/").replace("<!--", "\\u003c!--")


def render_visualization_html(template: str, payload: dict[str, Any]) -> str:
    if template.count(_DATA_MARKER) != 1:
        raise ValueError("Visualization template must contain exactly one __KB_DATA__ marker")
    rendered = template.replace(_DATA_MARKER, serialize_embedded_json(payload))
    first_kib = rendered.encode("utf-8")[:1024].lower()
    if b'<meta charset="utf-8">' not in first_kib:
        raise ValueError("Visualization template must declare UTF-8 in its first 1024 bytes")
    return rendered


def write_visualization_html(payload: dict[str, Any], output: Path, *, template: str | None = None) -> dict[str, Any]:
    warning = _export_zone_warning(output, content="document titles, URLs, dates and knowledge-graph topology")
    rendered = render_visualization_html(template if template is not None else load_visualization_template(), payload)
    encoded = rendered.encode("utf-8")
    _atomic_write(output, encoded)
    meta = payload.get("meta", {})
    meta_counts = meta.get("counts", {}) if isinstance(meta, dict) else {}
    stored_communities = meta_counts.get("communities") if isinstance(meta_counts, dict) else None
    if stored_communities is None:
        stored_communities = sum(
            row.get("key") != UNCLUSTERED_ZONE for row in payload.get("communities", []) if isinstance(row, dict)
        )
    result: dict[str, Any] = {
        "status": "ok",
        "output": str(output),
        "bytes": len(encoded),
        "documents": len(payload.get("documents", [])),
        "topics": len(payload.get("topics", [])),
        "communities": int(stored_communities),
        "isolated_documents": int(meta.get("isolated_documents", 0)),
        "status_counts": meta.get("status_counts", []),
    }
    if warning:
        result["warning"] = warning
    if len(encoded) > MAX_VIZ_BYTES:
        result["status"] = "degraded"
        result["warnings"] = [
            {
                "code": "artifact_size_exceeds_budget",
                "message": f"Offline HTML is {len(encoded)} bytes; the supported budget is {MAX_VIZ_BYTES} bytes.",
            }
        ]
    return result


def build_visualization_payload(
    repository: KnowledgeRepository,
    *,
    timeline_top_topics: int = 10,
    include_drafts: bool = False,
    built_at: str | None = None,
) -> dict[str, Any]:
    if timeline_top_topics < 1:
        raise ValueError("timeline_top_topics must be at least 1")

    documents = _visualization_documents(repository, include_drafts=include_drafts)
    document_by_key = {row["document_key"]: row for row in documents}
    memberships = document_topic_memberships(repository, include_drafts=include_drafts)
    similarity_projection = document_similarity_projection(repository, top_k=10, include_drafts=include_drafts)
    similarity = similarity_projection["edges"]
    topic_edges = topic_cooccurrence(repository, min_documents=2, include_drafts=include_drafts)
    communities = community_rollups(repository, include_drafts=include_drafts)
    timeline = timeline_buckets(
        repository,
        top_topics=timeline_top_topics,
        include_drafts=include_drafts,
    )

    topic_counts: Counter[str] = Counter()
    topic_labels: dict[str, str] = {}
    topics_by_document: dict[str, list[str]] = defaultdict(list)
    for membership in memberships:
        document_key = membership["document_key"]
        topic_key = membership["topic_key"]
        if document_key not in document_by_key:
            continue
        topic_counts[topic_key] += 1
        topic_labels[topic_key] = membership["topic_label"]
        topics_by_document[document_key].append(topic_key)

    community_by_document: dict[str, str] = {}
    community_documents: dict[str, list[str]] = {}
    for community_row in communities:
        key = community_row["community_key"]
        members = sorted(document for document in community_row["documents"] if document in document_by_key)
        community_documents[key] = members
        for document in members:
            community_by_document.setdefault(document, key)

    community_topic_counts: Counter[tuple[str, str]] = Counter()
    for membership in memberships:
        community_key = community_by_document.get(membership["document_key"])
        if community_key is not None:
            community_topic_counts[(community_key, membership["topic_key"])] += 1
    community_topic_edges: list[dict[str, Any]] = [
        {
            "source": community,
            "target": topic,
            "document_count": count,
        }
        for (community, topic), count in sorted(community_topic_counts.items())
    ]

    map_nodes = [f"community:{row['community_key']}" for row in communities]
    map_nodes.extend(f"topic:{key}" for key in sorted(topic_counts))
    map_edges = [
        (f"topic:{edge['source']}", f"topic:{edge['target']}", 1.0 + math.log1p(float(edge["document_count"])))
        for edge in topic_edges
    ]
    map_edges.extend(
        (
            f"community:{edge['source']}",
            f"topic:{edge['target']}",
            1.0 + math.log1p(float(edge["document_count"])),
        )
        for edge in community_topic_edges
    )
    map_positions = fruchterman_reingold(
        map_nodes,
        map_edges,
        seed=4,
        width=_MAP_WIDTH,
        height=_MAP_HEIGHT,
        iterations=50,
    )

    centers = {
        row["community_key"]: map_positions[f"community:{row['community_key']}"]
        for row in communities
        if f"community:{row['community_key']}" in map_positions
    }
    unclustered = sorted(set(document_by_key) - set(community_by_document))
    document_layout = phyllotaxis_layout(
        community_documents,
        centers,
        unclustered_documents=unclustered,
        point_spacing=3.2,
        point_radius=1.5,
        padding=8.0,
        min_disk_radius=26.0,
        unclustered_gap=30.0,
    )

    topic_payload = [
        {
            "id": f"topic:{key}",
            "key": key,
            "label": topic_labels[key],
            "documents": topic_counts[key],
            "x": round(map_positions[f"topic:{key}"][0], 3),
            "y": round(map_positions[f"topic:{key}"][1], 3),
        }
        for key in sorted(topic_counts)
    ]
    community_payload = []
    for row in communities:
        key = row["community_key"]
        position = map_positions[f"community:{key}"]
        disk = document_layout.disks[key]
        community_payload.append(
            {
                "id": f"community:{key}",
                "key": key,
                "label": row["label"],
                "size": row["size"],
                "summary": row["summary"],
                "top_topics": row["top_topics"],
                "x": round(position[0], 3),
                "y": round(position[1], 3),
                "radius": round(disk.radius, 3),
            }
        )
    if document_layout.unclustered_zone == UNCLUSTERED_ZONE:
        disk = document_layout.disks[UNCLUSTERED_ZONE]
        community_payload.append(
            {
                "id": f"community:{UNCLUSTERED_ZONE}",
                "key": UNCLUSTERED_ZONE,
                "label": "Unclustered",
                "size": len(unclustered),
                "summary": "Documents without a stored community membership",
                "top_topics": [],
                "x": round(disk.center[0], 3),
                "y": round(disk.center[1], 3),
                "radius": round(disk.radius, 3),
            }
        )

    document_payload: list[dict[str, Any]] = []
    document_indices: dict[str, int] = {}
    for index, key in enumerate(sorted(document_by_key)):
        row = document_by_key[key]
        x, y = document_layout.positions.get(key, (_MAP_WIDTH / 2.0, _MAP_HEIGHT / 2.0))
        document_indices[key] = index
        document_payload.append(
            {
                "index": index,
                "key": key,
                "title": row.get("title") or key,
                "url": safe_public_url(row.get("url")),
                "source_key": row.get("source_key"),
                "published_at": row.get("published_at"),
                "status": row.get("status"),
                "community": community_by_document.get(key, UNCLUSTERED_ZONE),
                "topics": sorted(set(topics_by_document.get(key, []))),
                "x": round(x, 3),
                "y": round(y, 3),
            }
        )
    similarity_payload = [
        [
            document_indices[edge["source"]],
            document_indices[edge["target"]],
            round(float(edge["weight"]), 3),
            int(edge["chunk_pairs"]),
        ]
        for edge in similarity
        if edge["source"] in document_indices and edge["target"] in document_indices
    ]
    ego_neighbors = [
        [
            document_indices[neighbor]
            for neighbor in similarity_projection["neighbors"].get(key, [])
            if neighbor in document_indices
        ]
        for key in sorted(document_by_key)
    ]

    metadata = _visualization_metadata(
        repository,
        include_drafts=include_drafts,
        built_at=built_at,
        selected_documents=len(document_payload),
        selected_topics=len(topic_payload),
        selected_communities=len(communities),
        selected_similarity_edges=len(similarity_payload),
        isolated_documents=len(unclustered),
    )
    return {
        "schema_version": VIZ_SCHEMA_VERSION,
        "meta": metadata,
        "sources": _visualization_sources(repository, {row.get("source_key") for row in documents}),
        "communities": community_payload,
        "topics": topic_payload,
        "documents": document_payload,
        "topic_edges": topic_edges,
        "community_topic_edges": community_topic_edges,
        "similarity_edges": similarity_payload,
        "ego_neighbors": ego_neighbors,
        "timeline": timeline,
    }


def build_visualization(
    repository: KnowledgeRepository,
    output: Path = DEFAULT_VIZ_OUTPUT,
    *,
    timeline_top_topics: int = 10,
    include_drafts: bool = False,
    built_at: str | None = None,
) -> dict[str, Any]:
    assert_visualization_ready(repository)
    payload = build_visualization_payload(
        repository,
        timeline_top_topics=timeline_top_topics,
        include_drafts=include_drafts,
        built_at=built_at,
    )
    result = write_visualization_html(payload, output)
    consistency_warnings = payload["meta"]["warnings"]
    combined_warnings = [*result.get("warnings", []), *consistency_warnings]
    if combined_warnings:
        result["status"] = "degraded"
        result["warnings"] = combined_warnings
    return result


def assert_visualization_ready(repository: KnowledgeRepository) -> None:
    report = health_report(repository.client)
    failed = [
        check
        for check in report.get("checks", [])
        if check.get("status") in {"error", "missing"} and check.get("name") != "vector_index"
    ]
    if report.get("status") == "error" or failed:
        raise RuntimeError("Knowledge base is not bootstrapped; run `kb platform bootstrap` before visualization")


def safe_public_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return value


def _visualization_documents(repository: KnowledgeRepository, *, include_drafts: bool) -> list[dict[str, Any]]:
    return repository.client.aql(
        """
        FOR document IN documents
          FILTER (@include_drafts AND document.status IN ["published", "draft"])
            OR (NOT @include_drafts AND document.status == "published")
          SORT document._key ASC
          RETURN {
            document_key: document._key,
            title: document.title,
            url: document.url,
            source_key: document.source_key,
            published_at: document.published_at,
            status: document.status
          }
        """,
        {"include_drafts": include_drafts},
    )


def _visualization_sources(repository: KnowledgeRepository, used: set[Any]) -> list[dict[str, Any]]:
    keys = sorted(key for key in used if isinstance(key, str))
    if not keys:
        return []
    return repository.client.aql(
        """
        FOR source IN sources
          FILTER source._key IN @keys
          SORT source._key ASC
          RETURN {
            key: source._key,
            label: source.display_name != null ? source.display_name : source._key,
            type: source.type
          }
        """,
        {"keys": keys},
    )


def _visualization_metadata(
    repository: KnowledgeRepository,
    *,
    include_drafts: bool,
    built_at: str | None,
    selected_documents: int,
    selected_topics: int,
    selected_communities: int,
    selected_similarity_edges: int,
    isolated_documents: int,
    warn_empty_similarity: bool = True,
) -> dict[str, Any]:
    diagnostics = repository.client.aql(
        """
        LET status_counts = (
          FOR document IN documents
            FILTER (@include_drafts AND document.status IN ["published", "draft"])
              OR (NOT @include_drafts AND document.status == "published")
            COLLECT status = document.status WITH COUNT INTO documents
            SORT status ASC
            RETURN { status: status, documents: documents }
        )
        LET embedding_models = (
          FOR chunk IN chunks
            FILTER chunk.embedding != null
            COLLECT model = chunk.embedding_model WITH COUNT INTO chunks
            SORT chunks DESC, model ASC
            RETURN { model: model, chunks: chunks }
        )
        RETURN {
          chunks: LENGTH(chunks),
          embedded_chunks: LENGTH(FOR chunk IN chunks FILTER chunk.embedding != null RETURN 1),
          selected_embedded_chunks: LENGTH(
            FOR chunk IN chunks
              FILTER chunk.embedding != null
              LET document = DOCUMENT("documents", chunk.document_key)
              FILTER document != null
              FILTER (@include_drafts AND document.status IN ["published", "draft"])
                OR (NOT @include_drafts AND document.status == "published")
              RETURN 1
          ),
          related_edges: LENGTH(
            FOR edge IN item_related_to_item FILTER edge.method == @related_method RETURN 1
          ),
          status_counts: status_counts,
          embedding_models: embedding_models
        }
        """,
        {"related_method": RELATED_EDGE_METHOD, "include_drafts": include_drafts},
    )[0]
    run_rows = repository.client.aql(
        """
        FOR target IN @targets
          LET run = FIRST(
            FOR candidate IN index_runs
              FILTER candidate.target == target AND candidate.status == "ok"
              SORT candidate.finished_at DESC, candidate.started_at DESC
              LIMIT 1
              RETURN KEEP(candidate, ["_key", "target", "status", "started_at", "finished_at", "counts"])
          )
          RETURN { target: target, run: run }
        """,
        {"targets": ["embeddings", "related", "communities"]},
    )
    index_runs = {row["target"]: row["run"] for row in run_rows}
    warnings: list[dict[str, str]] = []
    if warn_empty_similarity and selected_documents > 1 and not selected_similarity_edges:
        warnings.append(
            {
                "code": "related_index_empty",
                "message": (
                    "The selected visualization has no similarity edges; run "
                    "`kb index rebuild --target embeddings`, then `--target related`."
                ),
            }
        )
    warnings.extend(derived_index_stale_messages(index_runs))
    models = diagnostics["embedding_models"]
    return {
        "built_at": built_at or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "database": repository.client.settings.arango_database,
        "include_drafts": include_drafts,
        "embedding_model": models[0]["model"] if len(models) == 1 else None,
        "embedding_models": models,
        "index_runs": index_runs,
        "status_counts": diagnostics["status_counts"],
        "counts": {
            "documents": selected_documents,
            "chunks": diagnostics["chunks"],
            "topics": selected_topics,
            "communities": selected_communities,
            "similarity_edges": selected_similarity_edges,
            "related_edges_stored": diagnostics["related_edges"],
            "selected_embedded_chunks": diagnostics["selected_embedded_chunks"],
        },
        "isolated_documents": isolated_documents,
        "map": {"width": _MAP_WIDTH, "height": _MAP_HEIGHT},
        "thresholds": {
            "similarity_top_k": 10,
            "topic_cooccurrence_min_documents": 2,
            "artifact_max_bytes": MAX_VIZ_BYTES,
        },
        "warnings": warnings,
    }


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
