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
