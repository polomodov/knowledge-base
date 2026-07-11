import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import knowledge_base.viz_builder as builder
from knowledge_base.viz_builder import (
    assert_visualization_ready,
    build_visualization_payload,
    load_visualization_template,
    render_visualization_html,
    safe_public_url,
    serialize_embedded_json,
    write_visualization_html,
)

_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8"><title>KB</title></head>
<body><script type="application/json" id="kb-data">__KB_DATA__</script></body></html>"""


def test_packaged_template_loads_through_importlib_resources() -> None:
    template = load_visualization_template()
    assert '<meta charset="utf-8">' in template[:1024].lower()
    assert 'type="application/json" id="kb-data">__KB_DATA__</script>' in template
    assert 'id="map-canvas"' in template
    assert 'id="timeline-svg"' in template
    assert 'id="ego-svg"' in template


def test_packaged_template_hostile_unicode_round_trip() -> None:
    hostile = 'Кириллица 🚀 " & </script><!-- end'
    rendered = render_visualization_html(
        load_visualization_template(),
        {"meta": {}, "documents": [{"title": hostile}], "topics": [], "communities": []},
    )
    match = re.search(r'<script type="application/json" id="kb-data">(.*?)</script>', rendered, re.DOTALL)
    assert match is not None
    assert json.loads(match.group(1))["documents"][0]["title"] == hostile
    assert hostile not in rendered


def test_packaged_template_indexes_topic_and_community_nodes_by_raw_key_only() -> None:
    template = load_visualization_template()
    function = re.search(r"function indexNodes\(nodes\) \{(.*?)\n    \}", template, re.DOTALL)
    assert function is not None
    source = function.group(1)
    assert "node.key" in source
    assert "index" not in source
    assert "[node.id, node.key" not in source


def test_packaged_template_uses_python_ranked_ego_neighbors() -> None:
    template = load_visualization_template()
    assert "data.ego_neighbors" in template
    ego = re.search(r"function egoGraph\(center\) \{(.*?)\n    \}", template, re.DOTALL)
    assert ego is not None
    assert "egoNeighbors[center]" in ego.group(1)
    assert "uniqueDocumentIndices" in ego.group(1)


def test_embedded_json_is_script_safe_and_round_trips_byte_for_byte() -> None:
    title = 'Кириллица 🚀 " & </script><!-- marker'
    serialized = serialize_embedded_json({"title": title})
    assert "</script>" not in serialized
    assert "<!--" not in serialized
    assert json.loads(serialized)["title"] == title


def test_render_visualization_html_injects_data_once() -> None:
    rendered = render_visualization_html(_TEMPLATE, {"documents": [{"title": "Книга 📚"}]})
    match = re.search(r'<script type="application/json" id="kb-data">(.*?)</script>', rendered, re.DOTALL)
    assert match is not None
    assert json.loads(match.group(1))["documents"][0]["title"] == "Книга 📚"


def test_render_rejects_missing_or_duplicate_marker() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        render_visualization_html(_TEMPLATE.replace("__KB_DATA__", ""), {})
    with pytest.raises(ValueError, match="exactly one"):
        render_visualization_html(_TEMPLATE.replace("__KB_DATA__", "__KB_DATA____KB_DATA__"), {})


def test_render_requires_early_utf8_meta() -> None:
    template = "<html><head>" + (" " * 1100) + '<meta charset="utf-8"></head>__KB_DATA__</html>'
    with pytest.raises(ValueError, match="first 1024"):
        render_visualization_html(template, {})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://example.com/post", "https://example.com/post"),
        ("http://example.com", "http://example.com"),
        ("javascript:alert(1)", None),
        ("file:///tmp/private", None),
        ("https:///missing-host", None),
        (None, None),
    ],
)
def test_safe_public_url_allowlist(value, expected) -> None:
    assert safe_public_url(value) == expected


def test_write_visualization_html_reports_counts_and_bytes(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "knowledge-base.html"
    payload = {
        "meta": {
            "isolated_documents": 2,
            "status_counts": [{"status": "published", "documents": 1}],
            "counts": {"communities": 1},
        },
        "documents": [{"key": "a"}],
        "topics": [{"key": "t"}],
        "communities": [{"key": "stored"}, {"key": "unclustered"}],
    }
    result = write_visualization_html(payload, output, template=_TEMPLATE)
    assert result["status"] == "ok"
    assert result["documents"] == 1
    assert result["communities"] == 1
    assert result["isolated_documents"] == 2
    assert result["status_counts"] == [{"status": "published", "documents": 1}]
    assert result["bytes"] == len(output.read_bytes())
    assert result["warning"] == "output_outside_generated_zone"
    assert not list(output.parent.glob(".*.tmp"))


def test_write_visualization_html_warns_above_runtime_budget(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(builder, "MAX_VIZ_BYTES", 100)
    result = write_visualization_html(
        {"meta": {}, "documents": [{"title": "x" * 200}], "topics": [], "communities": []},
        tmp_path / "large.html",
        template=_TEMPLATE,
    )
    assert result["status"] == "degraded"
    assert [warning["code"] for warning in result["warnings"]] == ["artifact_size_exceeds_budget"]


def test_build_payload_uses_integer_edges_layouts_and_safe_urls(monkeypatch) -> None:
    documents = [
        {
            "document_key": "d1",
            "title": "Первый 🚀",
            "url": "https://example.test/1",
            "source_key": "source",
            "published_at": "2026-01-01T00:00:00Z",
            "status": "published",
        },
        {
            "document_key": "d2",
            "title": "Second",
            "url": "javascript:alert(1)",
            "source_key": "source",
            "published_at": "2026-02-01T00:00:00Z",
            "status": "published",
        },
    ]
    memberships = [
        {
            "document_key": "d1",
            "topic_key": "t1",
            "topic_label": "Topic",
            "source_key": "source",
            "published_at": "2026-01-01T00:00:00Z",
            "status": "published",
        },
        {
            "document_key": "d2",
            "topic_key": "t1",
            "topic_label": "Topic",
            "source_key": "source",
            "published_at": "2026-02-01T00:00:00Z",
            "status": "published",
        },
    ]
    monkeypatch.setattr(builder, "_visualization_documents", lambda repository, **kwargs: documents)
    monkeypatch.setattr(builder, "document_topic_memberships", lambda repository, **kwargs: memberships)
    monkeypatch.setattr(
        builder,
        "document_similarity_projection",
        lambda repository, **kwargs: {
            "edges": [{"source": "d1", "target": "d2", "weight": 0.8126, "chunk_pairs": 4}],
            "neighbors": {"d1": ["d2"], "d2": ["d1"]},
        },
    )
    monkeypatch.setattr(builder, "topic_cooccurrence", lambda repository, **kwargs: [])
    monkeypatch.setattr(
        builder,
        "community_rollups",
        lambda repository, **kwargs: [
            {
                "community_key": "c1",
                "label": "Topic",
                "size": 1,
                "summary": "summary",
                "top_topics": ["Topic"],
                "documents": ["d1"],
            }
        ],
    )
    monkeypatch.setattr(
        builder,
        "timeline_buckets",
        lambda repository, **kwargs: {
            "months": ["2026-01", "2026-02"],
            "by_source": [],
            "topics": [],
            "by_topic": [],
            "docs_without_dates": 0,
        },
    )
    monkeypatch.setattr(builder, "_visualization_sources", lambda repository, used: [{"key": "source", "label": "S"}])
    monkeypatch.setattr(
        builder,
        "_visualization_metadata",
        lambda repository, **kwargs: {
            "built_at": kwargs["built_at"],
            "isolated_documents": kwargs["isolated_documents"],
            "warnings": [],
            "map": {"width": 1800, "height": 1100},
        },
    )

    payload = build_visualization_payload(object(), built_at="2026-07-11T12:00:00Z")  # type: ignore[arg-type]

    assert [row["key"] for row in payload["documents"]] == ["d1", "d2"]
    assert payload["documents"][0]["url"] == "https://example.test/1"
    assert payload["documents"][1]["url"] is None
    assert payload["documents"][1]["community"] == "unclustered"
    assert payload["similarity_edges"] == [[0, 1, 0.813, 4]]
    assert payload["ego_neighbors"] == [[1], [0]]
    assert payload["meta"]["isolated_documents"] == 1
    assert all(isinstance(row["x"], float) and isinstance(row["y"], float) for row in payload["documents"])


def test_readiness_reports_clean_bootstrap_instruction(monkeypatch) -> None:
    monkeypatch.setattr(
        builder,
        "health_report",
        lambda client: {
            "status": "degraded",
            "checks": [{"name": "collection:documents", "status": "missing"}],
        },
    )
    repository = SimpleNamespace(client=object())
    with pytest.raises(RuntimeError, match="kb platform bootstrap"):
        assert_visualization_ready(repository)  # type: ignore[arg-type]


def test_metadata_warns_for_empty_selected_graph_and_stale_communities() -> None:
    class Client:
        settings = SimpleNamespace(arango_database="test")

        def aql(self, query, bind_vars=None):
            if "status_counts" in query:
                return [
                    {
                        "chunks": 2,
                        "embedded_chunks": 2,
                        "selected_embedded_chunks": 1,
                        "related_edges": 5,
                        "status_counts": [{"status": "published", "documents": 1}],
                        "embedding_models": [{"model": "hash-v1", "chunks": 2}],
                    }
                ]
            return [
                {"target": "embeddings", "run": None},
                {"target": "related", "run": {"finished_at": "2026-07-11T10:00:00Z"}},
                {"target": "communities", "run": {"finished_at": "2026-07-11T09:00:00Z"}},
            ]

    metadata = builder._visualization_metadata(
        SimpleNamespace(client=Client()),  # type: ignore[arg-type]
        include_drafts=False,
        built_at="2026-07-11T12:00:00Z",
        selected_documents=2,
        selected_topics=1,
        selected_communities=1,
        selected_similarity_edges=0,
        isolated_documents=0,
    )
    assert [warning["code"] for warning in metadata["warnings"]] == [
        "related_index_empty",
        "communities_older_than_related",
    ]
    ego_metadata = builder._visualization_metadata(
        SimpleNamespace(client=Client()),  # type: ignore[arg-type]
        include_drafts=False,
        built_at="2026-07-11T12:00:00Z",
        selected_documents=1,
        selected_topics=0,
        selected_communities=0,
        selected_similarity_edges=0,
        isolated_documents=1,
        warn_empty_similarity=False,
    )
    assert [warning["code"] for warning in ego_metadata["warnings"]] == ["communities_older_than_related"]
    single_document = builder._visualization_metadata(
        SimpleNamespace(client=Client()),  # type: ignore[arg-type]
        include_drafts=False,
        built_at="2026-07-11T12:00:00Z",
        selected_documents=1,
        selected_topics=0,
        selected_communities=0,
        selected_similarity_edges=0,
        isolated_documents=1,
    )
    assert [warning["code"] for warning in single_document["warnings"]] == ["communities_older_than_related"]

    class DraftOnlyClient(Client):
        def aql(self, query, bind_vars=None):
            rows = super().aql(query, bind_vars)
            if "status_counts" in query:
                rows[0]["selected_embedded_chunks"] = 0
            return rows

    draft_only = builder._visualization_metadata(
        SimpleNamespace(client=DraftOnlyClient()),  # type: ignore[arg-type]
        include_drafts=False,
        built_at="2026-07-11T12:00:00Z",
        selected_documents=2,
        selected_topics=0,
        selected_communities=0,
        selected_similarity_edges=0,
        isolated_documents=1,
    )
    assert "--target embeddings" in draft_only["warnings"][0]["message"]
