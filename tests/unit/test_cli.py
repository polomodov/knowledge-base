import json
import re
from types import SimpleNamespace
from typing import Any

import pytest

import knowledge_base.cli.main as cli
from knowledge_base.research_artifacts import (
    OutputRootAcknowledgementRequired,
    UnsafeArtifactPathError,
)
from knowledge_base.research_workflow import DossierBuildError, DossierBuildResult, ResearchRequest


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


def _build_result(
    request: ResearchRequest,
    evidence_candidate_builder,
    dossier_manifest_builder,
    *,
    status: str = "ready",
    warnings: tuple[str, ...] = (),
) -> DossierBuildResult:
    context = dossier_manifest_builder()["corpus_context"]
    if status == "no_evidence":
        candidates: tuple[dict[str, Any], ...] = ()
        selected_ids: tuple[str, ...] = ()
    else:
        candidate = evidence_candidate_builder()
        candidates = (candidate,)
        selected_ids = (candidate["citation"]["citation_id"],)
    return DossierBuildResult(
        status=status,
        request=request,
        candidate_evidence=candidates,
        selected_citation_ids=selected_ids,
        corpus_context=context,
        derived_context={"topics": (), "leads": ()},
        includes_drafts=request.includes_drafts,
        warnings=warnings,
    )


def _install_research_build_seams(
    monkeypatch,
    tmp_path,
    result: DossierBuildResult | Exception,
    *,
    output_warning: str | None = None,
    validation_error: Exception | None = None,
) -> tuple[dict[str, Any], Any]:
    calls: dict[str, Any] = {}
    settings = SimpleNamespace(repo_root=tmp_path / "repository")
    client = object()
    repository = object()
    provider = object()

    def settings_call(args):
        calls.setdefault("settings", []).append(args)
        return settings

    def client_call(value):
        calls["client_settings"] = value
        return client

    def repository_call(value):
        calls["repository_client"] = value
        return repository

    def provider_call(value):
        calls["provider_settings"] = value
        return provider

    def validate_call(output_root, *, generated_root, acknowledge_unsafe):
        calls["validate"] = {
            "output_root": output_root,
            "generated_root": generated_root,
            "acknowledge_unsafe": acknowledge_unsafe,
        }
        if validation_error is not None:
            raise validation_error
        return output_warning

    def build_call(actual_repository, request, **kwargs):
        calls["build"] = {"repository": actual_repository, "request": request, **kwargs}
        if isinstance(result, Exception):
            raise result
        return result

    def materialize_call(**kwargs):
        calls["materialize"] = kwargs
        assert not isinstance(result, Exception)
        manifest = {
            "dossier_key": "research-cli-synthetic-0123456789ab",
            "revision_id": "rev-20260712T120000Z-01234567",
            "content_digest": "a" * 64,
            "status": kwargs["status"],
            "selected_citation_ids": list(result.selected_citation_ids),
            "candidate_evidence": list(result.candidate_evidence),
            "includes_drafts": result.includes_drafts,
            "warnings": list(kwargs["warnings"]),
        }
        package = SimpleNamespace(manifest=manifest, files={})
        calls["package"] = package
        return package

    def publish_call(output_root, package):
        calls["publish"] = (output_root, package)
        return "created"

    monkeypatch.setattr(cli, "_settings", settings_call)
    monkeypatch.setattr(cli, "ArangoClient", client_call)
    monkeypatch.setattr(cli, "KnowledgeRepository", repository_call)
    monkeypatch.setattr(cli, "build_embedding_provider", provider_call)
    monkeypatch.setattr(cli, "validate_output_root", validate_call)
    monkeypatch.setattr(cli, "build_dossier", build_call)
    monkeypatch.setattr(cli, "materialize_dossier_package", materialize_call)
    monkeypatch.setattr(cli, "publish_dossier_package", publish_call)
    return calls, settings


def _read_cli_output(capsys) -> tuple[dict[str, Any], str]:
    captured = capsys.readouterr()
    return json.loads(captured.out), captured.err


@pytest.mark.parametrize(
    "arguments",
    [
        ["research", "build"],
        ["research", "build", "topic", "--documents", "nope"],
    ],
)
def test_research_build_parser_errors_use_json_exit_contract(capsys, arguments) -> None:
    code = cli.main(arguments)
    payload, stderr = _read_cli_output(capsys)

    assert code == 1
    assert payload["status"] == "error"
    assert "usage:" not in stderr.lower()


def test_research_build_defaults_wire_published_request_service_and_publication(
    capsys,
    monkeypatch,
    tmp_path,
    evidence_candidate_builder,
    dossier_manifest_builder,
) -> None:
    request = ResearchRequest(query="synthetic CLI research")
    result = _build_result(request, evidence_candidate_builder, dossier_manifest_builder)
    calls, settings = _install_research_build_seams(monkeypatch, tmp_path, result)

    code = cli.main(["research", "build", "synthetic CLI research"])
    payload, stderr = _read_cli_output(capsys)

    assert code == 0 and payload["status"] == "ok" and stderr == ""
    built_request = calls["build"]["request"]
    assert isinstance(built_request, ResearchRequest)
    assert built_request.visibility.value == "published_only"
    assert built_request.source_key is built_request.published_from is built_request.published_to is None
    assert (
        built_request.document_limit,
        built_request.fragments_per_document,
        built_request.evidence_limit,
        built_request.candidate_limit,
    ) == (12, 2, 24, 36)
    default_root = settings.repo_root / "data" / "generated" / "research"
    assert calls["validate"] == {
        "output_root": default_root,
        "generated_root": settings.repo_root / "data" / "generated",
        "acknowledge_unsafe": False,
    }
    assert calls["client_settings"] is settings
    assert calls["repository_client"] is not None and calls["provider_settings"] is settings
    assert calls["build"]["repository"] is not None
    assert calls["build"]["provider"] is not None
    timestamp_pattern = (
        r"(?a:\d){4}-(?a:\d){2}-(?a:\d){2}T"
        r"(?a:\d){2}:(?a:\d){2}:(?a:\d){2}(?:\.(?a:\d)+)?Z"
    )
    assert re.fullmatch(timestamp_pattern, calls["build"]["built_at"])
    assert calls["materialize"] == {
        "request": result.request,
        "corpus_context": result.corpus_context,
        "candidate_evidence": result.candidate_evidence,
        "derived_context": result.derived_context,
        "warnings": (),
        "status": "ready",
    }
    assert calls["publish"] == (default_root, calls["package"])
    assert (payload["evidence"], payload["candidates"], payload["includes_drafts"], payload["warnings"]) == (1, 1, False, [])
    assert payload["content_digest"] == "a" * 64
    assert payload["output"] == str(default_root / payload["dossier_key"] / "revisions" / payload["revision_id"])


def test_research_build_wires_all_scope_options_and_custom_output_acknowledgement(
    capsys,
    monkeypatch,
    tmp_path,
    evidence_candidate_builder,
    dossier_manifest_builder,
) -> None:
    request = ResearchRequest(
        query="scoped research",
        source_key="book-cube",
        published_from="2026-01-01",
        published_to="2026-02-28",
        visibility="published_and_drafts",
        document_limit=4,
        fragments_per_document=3,
    )
    result = _build_result(
        request,
        evidence_candidate_builder,
        dossier_manifest_builder,
        warnings=("draft_visibility_enabled",),
    )
    calls, _ = _install_research_build_seams(
        monkeypatch,
        tmp_path,
        result,
        output_warning="output_outside_generated_zone",
    )
    output_root = tmp_path / "external-research"

    code = cli.main(
        [
            "research",
            "build",
            "scoped research",
            "--output-root",
            str(output_root),
            "--acknowledge-unsafe-output",
            "--source",
            "book-cube",
            "--published-from",
            "2026-01-01",
            "--published-to",
            "2026-02-28",
            "--documents",
            "4",
            "--fragments-per-document",
            "3",
            "--include-drafts",
        ]
    )
    payload, stderr = _read_cli_output(capsys)

    built = calls["build"]["request"]
    assert code == 0 and built == request
    assert (built.evidence_limit, built.candidate_limit) == (12, 36)
    assert calls["validate"]["output_root"] == output_root
    assert calls["validate"]["acknowledge_unsafe"] is True
    assert calls["materialize"]["warnings"] == ("draft_visibility_enabled",)
    assert payload["status"] == "ok" and payload["includes_drafts"] is True
    assert payload["warnings"] == ["draft_visibility_enabled", "output_outside_generated_zone"]
    assert "draft_visibility_enabled" in stderr and "output_outside_generated_zone" in stderr


@pytest.mark.parametrize(
    "arguments",
    [
        ["   "],
        ["я" * 1001],
        ["topic", "--source", ""],
        ["topic", "--documents", "0"],
        ["topic", "--documents", "51"],
        ["topic", "--fragments-per-document", "0"],
        ["topic", "--fragments-per-document", "6"],
        ["topic", "--published-from", "not-a-date"],
        ["topic", "--published-from", "2026-03-02", "--published-to", "2026-03-01"],
    ],
)
def test_research_build_rejects_invalid_request_before_service_calls(
    capsys,
    monkeypatch,
    tmp_path,
    evidence_candidate_builder,
    dossier_manifest_builder,
    arguments,
) -> None:
    result = _build_result(ResearchRequest(query="valid"), evidence_candidate_builder, dossier_manifest_builder)
    calls, _ = _install_research_build_seams(monkeypatch, tmp_path, result)

    code = cli.main(["research", "build", *arguments])
    payload, _ = _read_cli_output(capsys)

    assert code == 1 and payload["status"] == "error" and payload["error_type"] == "ValueError"
    assert "build" not in calls and "materialize" not in calls and "publish" not in calls


@pytest.mark.parametrize(
    "policy_error",
    [
        OutputRootAcknowledgementRequired("custom output requires acknowledgement"),
        UnsafeArtifactPathError("symlink output is forbidden"),
    ],
)
def test_research_build_output_policy_failure_is_before_repo_provider_and_build(
    capsys,
    monkeypatch,
    tmp_path,
    evidence_candidate_builder,
    dossier_manifest_builder,
    policy_error,
) -> None:
    result = _build_result(ResearchRequest(query="safe"), evidence_candidate_builder, dossier_manifest_builder)
    calls, _ = _install_research_build_seams(monkeypatch, tmp_path, result, validation_error=policy_error)

    code = cli.main(["research", "build", "safe", "--output-root", str(tmp_path / "external")])
    payload, _ = _read_cli_output(capsys)

    assert code == 1 and payload["error_type"] == type(policy_error).__name__
    assert "validate" in calls
    assert not {"client_settings", "repository_client", "provider_settings", "build", "materialize", "publish"} & calls.keys()


@pytest.mark.parametrize(
    ("status", "warnings", "expected_exit", "published"),
    [
        ("degraded", ("optional related context is unavailable",), 0, True),
        ("no_evidence", (), 1, False),
    ],
)
def test_research_build_maps_service_outcomes_to_json_and_exit_status(
    capsys,
    monkeypatch,
    tmp_path,
    evidence_candidate_builder,
    dossier_manifest_builder,
    status,
    warnings,
    expected_exit,
    published,
) -> None:
    request = ResearchRequest(query="outcome")
    result = _build_result(
        request,
        evidence_candidate_builder,
        dossier_manifest_builder,
        status=status,
        warnings=warnings,
    )
    calls, _ = _install_research_build_seams(monkeypatch, tmp_path, result)

    code = cli.main(["research", "build", "outcome"])
    payload, stderr = _read_cli_output(capsys)

    assert code == expected_exit and payload["status"] == status
    assert payload["evidence"] == len(result.selected_citation_ids)
    assert payload["candidates"] == len(result.candidate_evidence)
    assert ("materialize" in calls and "publish" in calls) is published
    for warning in warnings:
        assert warning in payload["warnings"] and warning in stderr


def test_research_build_maps_service_error_without_materializing_or_publishing(
    capsys,
    monkeypatch,
    tmp_path,
) -> None:
    calls, _ = _install_research_build_seams(
        monkeypatch,
        tmp_path,
        DossierBuildError("required dossier evidence retrieval failed"),
    )

    code = cli.main(["research", "build", "error"])
    payload, _ = _read_cli_output(capsys)

    assert code == 1 and payload["status"] == "error" and payload["error_type"] == "DossierBuildError"
    assert "materialize" not in calls and "publish" not in calls


@pytest.mark.parametrize("failure_stage", ["build", "publish"])
def test_research_build_preserves_custom_location_warning_on_downstream_error(
    capsys,
    monkeypatch,
    tmp_path,
    evidence_candidate_builder,
    dossier_manifest_builder,
    failure_stage,
) -> None:
    request = ResearchRequest(query="warning on failure")
    result: DossierBuildResult | Exception
    if failure_stage == "build":
        result = DossierBuildError("required dossier evidence retrieval failed")
    else:
        result = _build_result(request, evidence_candidate_builder, dossier_manifest_builder)
    calls, _ = _install_research_build_seams(
        monkeypatch,
        tmp_path,
        result,
        output_warning="output_outside_generated_zone",
    )

    if failure_stage == "publish":

        def fail_publish(output_root, package):
            calls["publish_failed"] = (output_root, package)
            raise OSError("atomic publish failed")

        monkeypatch.setattr(cli, "publish_dossier_package", fail_publish)

    code = cli.main(
        [
            "research",
            "build",
            "warning on failure",
            "--output-root",
            str(tmp_path / "external-research"),
            "--acknowledge-unsafe-output",
        ]
    )
    payload, stderr = _read_cli_output(capsys)

    assert code == 1 and payload["status"] == "error"
    assert "output_outside_generated_zone" in payload["warnings"]
    assert "output_outside_generated_zone" in stderr
    if failure_stage == "build":
        assert "materialize" not in calls and "publish" not in calls
    else:
        assert "output_outside_generated_zone" not in calls["materialize"]["warnings"]
        assert "publish_failed" in calls
