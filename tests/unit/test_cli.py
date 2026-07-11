import json

import knowledge_base.cli.main as cli


def _emitted(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_main_reports_error_type_without_debug(capsys, monkeypatch) -> None:
    monkeypatch.delenv("KB_DEBUG", raising=False)
    # A missing --config file raises FileNotFoundError deep in the handler; the boundary keeps
    # the exception type and exits non-zero (finding #30).
    code = cli.main(["--config", "/no/such/config.toml", "platform", "health"])
    payload = _emitted(capsys)
    assert code == 1
    assert payload["error_type"] == "FileNotFoundError"
    assert "traceback" not in payload


def test_main_includes_traceback_under_kb_debug(capsys, monkeypatch) -> None:
    monkeypatch.setenv("KB_DEBUG", "1")
    cli.main(["--config", "/no/such/config.toml", "platform", "health"])
    assert "traceback" in _emitted(capsys)


def test_platform_up_exit_code_follows_status(capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli, "platform_up", lambda settings: {"status": "unavailable"})
    assert cli.main(["platform", "up"]) == 1
    monkeypatch.setattr(cli, "platform_up", lambda settings: {"status": "started", "services": {}})
    assert cli.main(["platform", "up"]) == 0


def test_platform_health_tolerates_degraded_vector_index_only(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "health_report",
        lambda client: {
            "status": "degraded",
            "checks": [{"name": "collection:documents", "status": "ok"}, {"name": "vector_index", "status": "degraded"}],
        },
    )
    assert cli.main(["platform", "health"]) == 0  # only the optional vector index is degraded


def test_platform_health_fails_when_core_component_missing(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "health_report",
        lambda client: {"status": "degraded", "checks": [{"name": "collection:documents", "status": "missing"}]},
    )
    assert cli.main(["platform", "health"]) == 1  # a missing core collection is not ready


def test_export_graph_wires_public_options_and_exit_status(capsys, monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_export(repository, output, **options):
        captured.update({"output": output, **options})
        return {"status": "ok", "nodes": 3, "edges": 2, "bytes": 100}

    monkeypatch.setattr(cli, "_repo", lambda args: object())
    monkeypatch.setattr(cli, "export_graph", fake_export)
    output = tmp_path / "graph.graphml"
    code = cli.main(
        [
            "export",
            "graph",
            "--format",
            "graphml",
            "--output",
            str(output),
            "--ego",
            "doc-1",
            "--topic-min-documents",
            "3",
            "--include-drafts",
        ]
    )
    assert code == 0
    assert captured == {
        "output": output,
        "output_format": "graphml",
        "include_drafts": True,
        "topic_min_documents": 3,
        "ego_document_key": "doc-1",
    }


def test_viz_build_uses_default_contract_and_degraded_exit(capsys, monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_build(repository, output, **options):
        captured.update({"output": output, **options})
        return {"status": "degraded", "warnings": [{"code": "related_index_empty"}]}

    monkeypatch.setattr(cli, "_repo", lambda args: object())
    monkeypatch.setattr(cli, "build_visualization", fake_build)
    output = tmp_path / "viz.html"
    code = cli.main(["viz", "build", "--output", str(output), "--timeline-top-topics", "7", "--include-drafts"])
    assert code == 1
    assert captured == {
        "output": output,
        "timeline_top_topics": 7,
        "include_drafts": True,
    }
