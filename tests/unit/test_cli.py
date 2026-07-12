import json
import re
from dataclasses import asdict
from types import SimpleNamespace
from typing import Any

import pytest

import knowledge_base.cli.main as cli
from knowledge_base.research_artifacts import (
    ArtifactContractError,
    OutputRootAcknowledgementRequired,
    UnsafeArtifactPathError,
)
from knowledge_base.research_workflow import (
    CurationOperation,
    DossierBuildError,
    DossierBuildResult,
    ResearchRequest,
    ValidationResult,
)


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


def _dossier_validation_result(
    *,
    status: str = "valid",
    citation_status: str = "valid",
    warnings: tuple[str, ...] = (),
) -> ValidationResult:
    resolved = citation_status == "valid"
    errors = () if status != "invalid" else (f"citation cit-0123456789abcdef is {citation_status}",)
    return ValidationResult(
        schema_version="1.0",
        artifact_type="validation_result",
        target_type="dossier_revision",
        target_id="rev-20260712T120000Z-01234567",
        target_digest="a" * 64,
        status=status,
        schema_valid=True,
        package_integrity=True,
        dossier_current=resolved,
        citations_resolved=resolved,
        coverage_complete=True,
        human_reviewed=False,
        citations=(
            {
                "citation_id": "cit-0123456789abcdef",
                "status": citation_status,
                "reason": None if resolved else "synthetic current-corpus mismatch",
            },
        ),
        warnings=warnings,
        errors=errors,
        validated_at="2026-07-12T12:05:00Z",
    )


def _json_dataclass(value) -> dict[str, Any]:
    return json.loads(json.dumps(asdict(value), ensure_ascii=False))


def _curation_parent_manifest(citation_builder, evidence_candidate_builder, dossier_manifest_builder):
    def candidate(label: str, *, state: str, rank: int) -> dict[str, Any]:
        excerpt = f"Synthetic {label} evidence for ordered CLI curation."
        citation = citation_builder(
            canonical_id=f"synthetic-{label}",
            document_key=f"doc-synthetic-{label}-0123456789ab",
            chunk_key=f"chunk-synthetic-{label}-0-0123456789ab",
            excerpt=excerpt,
            char_end=len(excerpt),
            url=f"https://example.test/synthetic-{label}",
        )
        return evidence_candidate_builder(
            citation=citation,
            document_rank=rank,
            fragment_rank=1,
            selection_state=state,
            selection_reason="automatic-round-1" if state == "selected" else "bounded-candidate-pool",
        )

    candidates = [
        candidate("exclude", state="selected", rank=1),
        candidate("include-first", state="candidate", rank=2),
        candidate("pin", state="selected", rank=3),
        candidate("include-second", state="candidate", rank=4),
    ]
    manifest = dossier_manifest_builder(candidate_evidence=candidates)
    citation_ids = {
        row["citation"]["canonical_id"].removeprefix("synthetic-"): row["citation"]["citation_id"] for row in candidates
    }
    return manifest, citation_ids


def _curation_error(
    code: str,
    *,
    parent_validation: ValidationResult | None = None,
) -> Exception:
    error_type = vars(cli).get("DossierCurationError")
    if error_type is None:
        raise AssertionError("research CLI must expose DossierCurationError")
    return error_type(
        "private curation diagnostic must not cross the CLI boundary",
        code=code,
        parent_validation=parent_validation,
    )


def _install_research_revision_seams(
    monkeypatch,
    tmp_path,
    parent_manifest: dict[str, Any],
    validation: ValidationResult,
    *,
    output_warning: str | None = None,
    output_policy_error: Exception | None = None,
    load_error: Exception | None = None,
    curation_error_code: str | None = None,
) -> tuple[dict[str, Any], Any]:
    calls: dict[str, Any] = {"order": []}
    assert parent_manifest["revision_id"] == validation.target_id
    parent_manifest["content_digest"] = validation.target_digest
    settings = SimpleNamespace(repo_root=tmp_path / "repository")
    repository = object()
    parent_package = SimpleNamespace(manifest=parent_manifest, validation={}, markdown="", files={})
    curation_result = SimpleNamespace(
        dossier_key=parent_manifest["dossier_key"],
        parent_revision_id=parent_manifest["revision_id"],
        curation_operations=(),
        parent_validation=validation,
    )
    child_manifest = {
        "dossier_key": parent_manifest["dossier_key"],
        "revision_id": "rev-20260712T121000Z-89abcdef",
        "parent_revision_id": parent_manifest["revision_id"],
        "content_digest": "b" * 64,
        "selected_citation_ids": list(parent_manifest["selected_citation_ids"]),
        "candidate_evidence": list(parent_manifest["candidate_evidence"]),
        "curation_operations": [],
        "includes_drafts": parent_manifest["includes_drafts"],
        "warnings": [],
    }
    child_package = SimpleNamespace(manifest=child_manifest, validation={}, markdown="", files={})

    def settings_call(args):
        calls["order"].append("settings")
        calls["settings"] = args
        return settings

    def client_call(actual_settings):
        calls["order"].append("client")
        calls["client_settings"] = actual_settings
        return object()

    def repository_call(client):
        calls["order"].append("repository")
        calls["repository_client"] = client
        return repository

    def output_root_call(output_root, *, generated_root, acknowledge_unsafe):
        calls["order"].append("validate_output_root")
        calls["validate_output_root"] = {
            "output_root": output_root,
            "generated_root": generated_root,
            "acknowledge_unsafe": acknowledge_unsafe,
        }
        if output_policy_error is not None:
            raise output_policy_error
        return output_warning

    def load_call(revision_path):
        calls["order"].append("load")
        calls["load"] = revision_path
        if load_error is not None:
            raise load_error
        return parent_package

    def validate_call(actual_repository, revision, *, validated_at):
        calls["order"].append("validate")
        calls["validate"] = {
            "repository": actual_repository,
            "revision": revision,
            "validated_at": validated_at,
        }
        return validation

    def curate_call(actual_repository, parent_revision, operations, *, validated_at):
        calls["order"].append("curate")
        calls["curate"] = {
            "repository": actual_repository,
            "parent_revision": parent_revision,
            "operations": operations,
            "validated_at": validated_at,
        }
        if curation_error_code is not None:
            raise _curation_error(
                curation_error_code,
                parent_validation=validation if curation_error_code == "parent_not_current" else None,
            )
        curation_result.curation_operations = operations
        return curation_result

    def materialize_call(parent_package, result):
        calls["order"].append("materialize")
        calls["materialize"] = {
            "parent_package": parent_package,
            "result": result,
        }
        child_manifest["curation_operations"] = [asdict(operation) for operation in result.curation_operations]
        return child_package

    def publish_call(output_root, package):
        calls["order"].append("publish")
        calls["publish"] = (output_root, package)
        return "created"

    def forbidden_retrieval(*args, **kwargs):
        pytest.fail("validation and curation must not start dossier retrieval")

    monkeypatch.setattr(cli, "_settings", settings_call)
    monkeypatch.setattr(cli, "ArangoClient", client_call)
    monkeypatch.setattr(cli, "KnowledgeRepository", repository_call)
    monkeypatch.setattr(cli, "validate_output_root", output_root_call)
    monkeypatch.setattr(cli, "load_dossier_package", load_call, raising=False)
    monkeypatch.setattr(cli, "validate_dossier_revision", validate_call, raising=False)
    monkeypatch.setattr(cli, "curate_dossier_revision", curate_call, raising=False)
    monkeypatch.setattr(cli, "materialize_curated_dossier_package", materialize_call, raising=False)
    monkeypatch.setattr(cli, "publish_dossier_package", publish_call)
    monkeypatch.setattr(cli, "build_dossier", forbidden_retrieval)
    monkeypatch.setattr(cli, "build_embedding_provider", forbidden_retrieval)
    return calls, settings


@pytest.mark.parametrize(
    "arguments",
    [
        ["research", "validate"],
        ["research", "curate"],
    ],
)
def test_research_validate_and_curate_parser_errors_use_json_exit_contract(capsys, arguments) -> None:
    code = cli.main(arguments)
    payload, stderr = _read_cli_output(capsys)

    assert code == 1 and payload["status"] == "error"
    assert "usage:" not in stderr.lower()


@pytest.mark.parametrize(
    ("validation", "expected_exit"),
    [
        (_dossier_validation_result(), 0),
        (_dossier_validation_result(status="valid_with_warnings", warnings=("synthetic_validation_warning",)), 0),
        (_dossier_validation_result(status="invalid", citation_status="changed"), 1),
    ],
    ids=["valid", "valid-with-warnings", "invalid"],
)
def test_research_validate_emits_stable_result_and_contract_exit(
    capsys,
    monkeypatch,
    tmp_path,
    dossier_manifest_builder,
    validation,
    expected_exit,
) -> None:
    parent_manifest = dossier_manifest_builder()
    calls, _ = _install_research_revision_seams(monkeypatch, tmp_path, parent_manifest, validation)
    revision = tmp_path / "immutable-parent"

    code = cli.main(["research", "validate", str(revision), "--output-root", str(tmp_path / "research-root")])
    captured = capsys.readouterr()
    expected = _json_dataclass(validation)

    assert code == expected_exit
    assert captured.out == json.dumps(expected, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    for warning in validation.warnings:
        assert warning in captured.err
    if not validation.warnings:
        assert captured.err == ""
    assert calls["load"] == revision
    assert calls["validate"]["repository"] is not None
    assert calls["validate"]["revision"].revision_id == parent_manifest["revision_id"]
    assert calls["order"].index("load") < calls["order"].index("repository") < calls["order"].index("validate")
    assert "validate_output_root" not in calls
    assert not {"curate", "materialize", "publish"} & calls.keys()


def test_research_validate_rejects_bad_package_before_corpus_access(
    capsys,
    monkeypatch,
    tmp_path,
    dossier_manifest_builder,
) -> None:
    validation = _dossier_validation_result()
    calls, _ = _install_research_revision_seams(
        monkeypatch,
        tmp_path,
        dossier_manifest_builder(),
        validation,
        load_error=ArtifactContractError("synthetic package integrity failure"),
    )

    code = cli.main(["research", "validate", str(tmp_path / "broken-revision")])
    payload, _ = _read_cli_output(capsys)

    assert code == 1 and payload["status"] == "error" and payload["error_type"] == "ArtifactContractError"
    assert calls["order"] == ["load"]
    assert not {"client", "repository", "validate", "curate", "materialize", "publish"} & calls.keys()


def test_research_curate_preserves_mixed_repeated_operation_order_and_publishes_stable_json(
    capsys,
    monkeypatch,
    tmp_path,
    citation_builder,
    evidence_candidate_builder,
    dossier_manifest_builder,
) -> None:
    parent_manifest, citation_ids = _curation_parent_manifest(
        citation_builder,
        evidence_candidate_builder,
        dossier_manifest_builder,
    )
    validation = _dossier_validation_result()
    calls, _ = _install_research_revision_seams(
        monkeypatch,
        tmp_path,
        parent_manifest,
        validation,
        output_warning="output_outside_generated_zone",
    )
    revision = tmp_path / "immutable-parent"
    output_root = tmp_path / "acknowledged-external-research"
    reason = "owner ordered evidence review"

    code = cli.main(
        [
            "research",
            "curate",
            str(revision),
            "--exclude",
            citation_ids["exclude"],
            "--include",
            citation_ids["include-first"],
            "--pin",
            citation_ids["pin"],
            "--include",
            citation_ids["include-second"],
            "--reason",
            reason,
            "--output-root",
            str(output_root),
            "--acknowledge-unsafe-output",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    curation_result = calls["materialize"]["result"]
    expected = {
        "status": "ok",
        "dossier_key": parent_manifest["dossier_key"],
        "revision_id": "rev-20260712T121000Z-89abcdef",
        "parent_revision_id": parent_manifest["revision_id"],
        "content_digest": "b" * 64,
        "output": str(output_root / parent_manifest["dossier_key"] / "revisions" / "rev-20260712T121000Z-89abcdef"),
        "operations": 4,
        "includes_drafts": False,
        "warnings": ["output_outside_generated_zone"],
    }

    assert captured.out == json.dumps(expected, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    assert "output_outside_generated_zone" in captured.err
    assert calls["validate_output_root"] == {
        "output_root": output_root,
        "generated_root": calls["client_settings"].repo_root / "data" / "generated",
        "acknowledge_unsafe": True,
    }
    operations = calls["curate"]["operations"]
    assert all(isinstance(operation, CurationOperation) for operation in operations)
    assert [(operation.operation, operation.citation_id, operation.reason, operation.ordinal) for operation in operations] == [
        ("exclude", citation_ids["exclude"], reason, 0),
        ("include", citation_ids["include-first"], reason, 1),
        ("pin", citation_ids["pin"], reason, 2),
        ("include", citation_ids["include-second"], reason, 3),
    ]
    assert calls["curate"]["repository"] is not None
    assert calls["curate"]["validated_at"].endswith("Z")
    assert curation_result.parent_validation is validation
    assert curation_result.curation_operations is operations
    assert "validate" not in calls
    assert calls["publish"][0] == output_root
    assert calls["order"] == [
        "settings",
        "validate_output_root",
        "load",
        "client",
        "repository",
        "curate",
        "materialize",
        "publish",
    ]


def test_research_curate_rejects_empty_operation_list_before_io(
    capsys,
    monkeypatch,
    tmp_path,
    dossier_manifest_builder,
) -> None:
    calls, _ = _install_research_revision_seams(
        monkeypatch,
        tmp_path,
        dossier_manifest_builder(),
        _dossier_validation_result(),
    )

    code = cli.main(["research", "curate", str(tmp_path / "immutable-parent")])
    payload, _ = _read_cli_output(capsys)

    assert code == 1 and payload["status"] == "error"
    assert calls["order"] == []


@pytest.mark.parametrize(
    "policy_error",
    [
        OutputRootAcknowledgementRequired("custom output requires acknowledgement"),
        UnsafeArtifactPathError("symlink output is forbidden"),
    ],
)
def test_research_curate_output_policy_failure_precedes_loading_and_corpus_access(
    capsys,
    monkeypatch,
    tmp_path,
    dossier_manifest_builder,
    policy_error,
) -> None:
    calls, _ = _install_research_revision_seams(
        monkeypatch,
        tmp_path,
        dossier_manifest_builder(),
        _dossier_validation_result(),
        output_policy_error=policy_error,
    )

    code = cli.main(
        [
            "research",
            "curate",
            str(tmp_path / "immutable-parent"),
            "--include",
            "cit-0123456789abcdef",
            "--output-root",
            str(tmp_path / "external-research"),
        ]
    )
    payload, _ = _read_cli_output(capsys)

    assert code == 1 and payload["status"] == "error" and payload["error_type"] == type(policy_error).__name__
    assert calls["order"] == ["settings", "validate_output_root"]
    assert not {"load", "client", "repository", "validate", "curate", "materialize", "publish"} & calls.keys()


def test_research_curate_parent_current_gate_returns_safe_rejection_and_does_not_publish(
    capsys,
    monkeypatch,
    tmp_path,
    dossier_manifest_builder,
) -> None:
    validation = _dossier_validation_result(status="invalid", citation_status="hidden")
    parent_manifest = dossier_manifest_builder()
    calls, _ = _install_research_revision_seams(
        monkeypatch,
        tmp_path,
        parent_manifest,
        validation,
        output_warning="output_outside_generated_zone",
        curation_error_code="parent_not_current",
    )
    output_root = tmp_path / "acknowledged-external-research"

    code = cli.main(
        [
            "research",
            "curate",
            str(tmp_path / "immutable-parent"),
            "--exclude",
            parent_manifest["selected_citation_ids"][0],
            "--output-root",
            str(output_root),
            "--acknowledge-unsafe-output",
        ]
    )
    captured = capsys.readouterr()
    expected = {
        "status": "rejected",
        "reason": "parent_not_current",
        "validation": _json_dataclass(validation),
        "warnings": ["output_outside_generated_zone"],
    }

    assert code == 1
    assert captured.out == json.dumps(expected, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    assert "output_outside_generated_zone" in captured.err
    assert calls["curate"]["repository"] is not None
    assert "validate" not in calls
    assert not {"materialize", "publish"} & calls.keys()
    assert "private curation diagnostic" not in captured.out


def test_research_curate_service_rejection_exposes_only_safe_code(
    capsys,
    monkeypatch,
    tmp_path,
    dossier_manifest_builder,
) -> None:
    parent_manifest = dossier_manifest_builder()
    calls, _ = _install_research_revision_seams(
        monkeypatch,
        tmp_path,
        parent_manifest,
        _dossier_validation_result(),
        curation_error_code="unknown_citation",
    )

    code = cli.main(
        [
            "research",
            "curate",
            str(tmp_path / "immutable-parent"),
            "--include",
            "cit-ffffffffffffffff",
        ]
    )
    captured = capsys.readouterr()
    expected = {"status": "rejected", "reason": "unknown_citation", "warnings": []}

    assert code == 1
    assert captured.out == json.dumps(expected, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    assert captured.err == ""
    assert "private curation diagnostic" not in captured.out
    assert "validate" not in calls
    assert not {"materialize", "publish"} & calls.keys()


class _SafeWritingHandoffFailure(Exception):
    def __init__(self, code: str, validation: ValidationResult | None = None) -> None:
        super().__init__("private handoff diagnostic must not cross the CLI boundary")
        self.code = code
        self.validation = validation


class _SafeWritingImportFailure(Exception):
    def __init__(self, code: str, validation: ValidationResult | None = None) -> None:
        super().__init__("private import diagnostic must not cross the CLI boundary")
        self.code = code
        self.validation = validation


def _writing_validation_result(
    target_type: str,
    target_id: str,
    target_digest: str,
    *,
    status: str = "valid",
    warnings: tuple[str, ...] = (),
) -> ValidationResult:
    valid = status != "invalid"
    return ValidationResult(
        schema_version="1.0",
        artifact_type="validation_result",
        target_type=target_type,
        target_id=target_id,
        target_digest=target_digest,
        status=status,
        schema_valid=valid,
        package_integrity=valid,
        dossier_current=valid,
        citations_resolved=valid,
        coverage_complete=valid,
        human_reviewed=False,
        citations=(
            {
                "citation_id": "cit-0123456789abcdef",
                "status": "valid" if valid else "changed",
                "reason": None if valid else "synthetic current-corpus mismatch",
            },
        ),
        warnings=warnings,
        errors=() if valid else ("writing artifact is not current",),
        validated_at="2026-07-12T12:10:00Z",
    )


def _install_research_handoff_seams(
    monkeypatch,
    tmp_path,
    dossier_manifest: dict[str, Any],
    handoff_payload: dict[str, Any],
    *,
    publication_status: str = "created",
    location_warning: str | None = None,
    publication_error: Exception | None = None,
    failure_code: str | None = None,
    failure_validation: ValidationResult | None = None,
) -> tuple[dict[str, Any], Any]:
    calls: dict[str, Any] = {"order": []}
    settings = SimpleNamespace(repo_root=tmp_path / "repository")
    repository = object()
    dossier_package = SimpleNamespace(manifest=dossier_manifest, validation={}, markdown="", files={})
    handoff_package = SimpleNamespace(**handoff_payload)

    def settings_call(args):
        calls["order"].append("settings")
        calls["settings"] = args
        return settings

    def client_call(actual_settings):
        calls["order"].append("client")
        calls["client_settings"] = actual_settings
        return object()

    def repository_call(client):
        calls["order"].append("repository")
        calls["repository_client"] = client
        return repository

    def load_dossier_call(path):
        calls["order"].append("load_dossier")
        calls["load_dossier"] = path
        return dossier_package

    def build_call(actual_repository, revision, requested, **kwargs):
        calls["order"].append("build_handoff")
        calls["build_handoff"] = {
            "repository": actual_repository,
            "revision": revision,
            "requested": requested,
            **kwargs,
        }
        if failure_code is not None:
            raise _SafeWritingHandoffFailure(failure_code, failure_validation)
        return handoff_package

    def publish_call(output_root, package, *, generated_root, acknowledge_unsafe):
        calls["order"].append("publish_handoff")
        calls["publish_handoff"] = {
            "output_root": output_root,
            "package": package,
            "generated_root": generated_root,
            "acknowledge_unsafe": acknowledge_unsafe,
        }
        if publication_error is not None:
            raise publication_error
        path = output_root / package.dossier_key / "handoffs" / f"{package.handoff_id}.json"
        return SimpleNamespace(
            status=publication_status,
            path=path,
            package=package,
            location_warning=location_warning,
        )

    monkeypatch.setattr(cli, "_settings", settings_call)
    monkeypatch.setattr(cli, "ArangoClient", client_call)
    monkeypatch.setattr(cli, "KnowledgeRepository", repository_call)
    monkeypatch.setattr(cli, "load_dossier_package", load_dossier_call)
    monkeypatch.setattr(cli, "build_writing_handoff", build_call, raising=False)
    monkeypatch.setattr(cli, "publish_writing_handoff", publish_call, raising=False)
    monkeypatch.setattr(cli, "WritingHandoffError", _SafeWritingHandoffFailure, raising=False)
    return calls, settings


@pytest.mark.parametrize(
    "arguments",
    [
        ["research", "handoff"],
        ["research", "handoff", "revision", "--output-kind", "article"],
        ["research", "import-output"],
        ["research", "import-output", "package.json"],
    ],
)
def test_research_writing_commands_parser_errors_use_json_exit_contract(capsys, arguments) -> None:
    code = cli.main(arguments)
    payload, stderr = _read_cli_output(capsys)

    assert code == 1 and payload["status"] == "error"
    assert "usage:" not in stderr.lower()


@pytest.mark.parametrize(
    ("output_kind", "publication_status"),
    [("draft", "created"), ("summary", "reused")],
)
def test_research_handoff_wires_both_output_kinds_options_and_stable_publication_json(
    capsys,
    monkeypatch,
    tmp_path,
    dossier_manifest_builder,
    handoff_package_builder,
    requested_output_builder,
    output_kind,
    publication_status,
) -> None:
    dossier_manifest = dossier_manifest_builder()
    requested_payload = requested_output_builder(
        kind=output_kind,
        language="en-GB",
        style="concise synthetic style",
        max_words=640,
    )
    handoff_payload = handoff_package_builder(
        dossier_manifest=dossier_manifest,
        requested_output=requested_payload,
        warnings=["exact_evidence_requires_owner_review"],
    )
    calls, settings = _install_research_handoff_seams(
        monkeypatch,
        tmp_path,
        dossier_manifest,
        handoff_payload,
        publication_status=publication_status,
    )
    revision = tmp_path / "immutable-revision"

    code = cli.main(
        [
            "research",
            "handoff",
            str(revision),
            "--output-kind",
            output_kind,
            "--language",
            "en-GB",
            "--style",
            "concise synthetic style",
            "--max-words",
            "640",
            "--acknowledge-external-disclosure",
        ]
    )
    captured = capsys.readouterr()
    default_root = settings.repo_root / "data" / "generated" / "research"
    expected = {
        "status": "ok",
        "handoff_id": handoff_payload["handoff_id"],
        "dossier_key": dossier_manifest["dossier_key"],
        "revision_id": dossier_manifest["revision_id"],
        "package_digest": handoff_payload["package_digest"],
        "output": str(default_root / dossier_manifest["dossier_key"] / "handoffs" / f"{handoff_payload['handoff_id']}.json"),
        "output_kind": output_kind,
        "evidence": len(handoff_payload["evidence"]),
        "includes_drafts": False,
        "egress_acknowledged": True,
        "draft_evidence_acknowledged": False,
        "warnings": ["exact_evidence_requires_owner_review"],
    }

    assert code == 0
    assert captured.out == json.dumps(expected, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    assert "exact_evidence_requires_owner_review" in captured.err
    assert calls["load_dossier"] == revision
    build = calls["build_handoff"]
    requested = build["requested"]
    assert type(requested).__name__ == "RequestedWritingOutput"
    assert (requested.kind, requested.language, requested.style, requested.max_words) == (
        output_kind,
        "en-GB",
        "concise synthetic style",
        640,
    )
    assert build["repository"] is not None
    assert build["revision"].revision_id == dossier_manifest["revision_id"]
    assert build["egress_acknowledged"] is True
    assert build["allow_draft_evidence"] is False
    assert build["validated_at"].endswith("Z") and build["created_at"].endswith("Z")
    assert calls["publish_handoff"] == {
        "output_root": default_root,
        "package": calls["publish_handoff"]["package"],
        "generated_root": settings.repo_root / "data" / "generated",
        "acknowledge_unsafe": False,
    }
    assert calls["publish_handoff"]["package"] is not None


def test_research_handoff_keeps_location_and_external_disclosure_acknowledgements_separate(
    capsys,
    monkeypatch,
    tmp_path,
    dossier_manifest_builder,
    handoff_package_builder,
) -> None:
    dossier_manifest = dossier_manifest_builder()
    handoff_payload = handoff_package_builder(
        dossier_manifest=dossier_manifest,
        warnings=["exact_evidence_requires_owner_review"],
    )
    calls, settings = _install_research_handoff_seams(
        monkeypatch,
        tmp_path,
        dossier_manifest,
        handoff_payload,
        location_warning="output_outside_generated_zone",
    )
    output_root = tmp_path / "acknowledged-external-handoffs"

    code = cli.main(
        [
            "research",
            "handoff",
            str(tmp_path / "immutable-revision"),
            "--output-root",
            str(output_root),
            "--acknowledge-unsafe-output",
            "--acknowledge-external-disclosure",
        ]
    )
    payload, stderr = _read_cli_output(capsys)

    assert code == 0
    assert calls["build_handoff"]["egress_acknowledged"] is True
    assert calls["publish_handoff"]["acknowledge_unsafe"] is True
    assert calls["publish_handoff"]["generated_root"] == settings.repo_root / "data" / "generated"
    assert payload["warnings"] == ["exact_evidence_requires_owner_review", "output_outside_generated_zone"]
    assert "exact_evidence_requires_owner_review" in stderr and "output_outside_generated_zone" in stderr


def test_research_handoff_external_disclosure_ack_does_not_acknowledge_custom_output_root(
    capsys,
    monkeypatch,
    tmp_path,
    dossier_manifest_builder,
    handoff_package_builder,
) -> None:
    dossier_manifest = dossier_manifest_builder()
    handoff_payload = handoff_package_builder(dossier_manifest=dossier_manifest, warnings=[])
    calls, _ = _install_research_handoff_seams(
        monkeypatch,
        tmp_path,
        dossier_manifest,
        handoff_payload,
        publication_error=OutputRootAcknowledgementRequired("custom output requires acknowledgement"),
    )

    code = cli.main(
        [
            "research",
            "handoff",
            str(tmp_path / "immutable-revision"),
            "--output-root",
            str(tmp_path / "unacknowledged-external-handoffs"),
            "--acknowledge-external-disclosure",
        ]
    )
    payload, _ = _read_cli_output(capsys)

    assert code == 1 and payload["error_type"] == "OutputRootAcknowledgementRequired"
    assert calls["build_handoff"]["egress_acknowledged"] is True
    assert calls["publish_handoff"]["acknowledge_unsafe"] is False


@pytest.mark.parametrize(
    ("includes_drafts", "egress_acknowledged", "allow_draft_evidence", "expected_code"),
    [
        (False, False, False, "external_disclosure_not_acknowledged"),
        (False, True, False, None),
        (True, True, False, "draft_evidence_not_acknowledged"),
        (True, True, True, None),
    ],
    ids=["published-needs-egress", "published-egress-ok", "draft-needs-second-ack", "draft-both-acks-ok"],
)
def test_research_handoff_acknowledgement_truth_table_and_safe_rejections(
    capsys,
    monkeypatch,
    tmp_path,
    research_request_builder,
    dossier_manifest_builder,
    handoff_package_builder,
    includes_drafts,
    egress_acknowledged,
    allow_draft_evidence,
    expected_code,
) -> None:
    request = research_request_builder(
        visibility="published_and_drafts" if includes_drafts else "published_only",
    )
    dossier_manifest = dossier_manifest_builder(request=request)
    handoff_payload = handoff_package_builder(dossier_manifest=dossier_manifest, warnings=[])
    calls, _ = _install_research_handoff_seams(
        monkeypatch,
        tmp_path,
        dossier_manifest,
        handoff_payload,
        failure_code=expected_code,
    )
    arguments = ["research", "handoff", str(tmp_path / "immutable-revision")]
    if egress_acknowledged:
        arguments.append("--acknowledge-external-disclosure")
    if allow_draft_evidence:
        arguments.append("--allow-draft-evidence")

    code = cli.main(arguments)
    payload, stderr = _read_cli_output(capsys)

    build = calls["build_handoff"]
    assert build["egress_acknowledged"] is egress_acknowledged
    assert build["allow_draft_evidence"] is allow_draft_evidence
    if expected_code is None:
        assert code == 0 and payload["status"] == "ok"
        assert "publish_handoff" in calls
    else:
        assert code == 1
        assert payload == {"status": "rejected", "reason": expected_code, "warnings": []}
        assert stderr == ""
        assert "private handoff diagnostic" not in json.dumps(payload)
        assert "publish_handoff" not in calls


def _install_research_import_seams(
    monkeypatch,
    tmp_path,
    dossier_manifest: dict[str, Any],
    handoff_payload: dict[str, Any],
    output_payload: dict[str, Any],
    validation: ValidationResult,
    *,
    publication_status: str = "created",
    location_warning: str | None = None,
    output_policy_error: Exception | None = None,
    failure_code: str | None = None,
) -> tuple[dict[str, Any], Any]:
    calls: dict[str, Any] = {"order": []}
    settings = SimpleNamespace(repo_root=tmp_path / "repository")
    repository = object()
    dossier_package = SimpleNamespace(manifest=dossier_manifest, validation={}, markdown="", files={})
    handoff_package = SimpleNamespace(**handoff_payload)
    writing_output = SimpleNamespace(**output_payload)
    import_result = SimpleNamespace(handoff=handoff_package, output=writing_output, validation=validation)
    unsupported_sections = sum(bool(section["unsupported_by_corpus"]) for section in output_payload["sections"])
    imported_manifest = {
        "schema_version": "1.0",
        "artifact_type": "imported_writing",
        "writing_id": "writing-0123456789abcdef",
        "output_kind": output_payload["output_kind"],
        "incoming_package_digest": output_payload["package_digest"],
        "handoff_id": handoff_payload["handoff_id"],
        "handoff_digest": handoff_payload["package_digest"],
        "dossier_key": dossier_manifest["dossier_key"],
        "revision_id": dossier_manifest["revision_id"],
        "revision_content_digest": dossier_manifest["content_digest"],
        "visibility": handoff_payload["visibility"],
        "includes_drafts": handoff_payload["includes_drafts"],
        "egress_acknowledged": handoff_payload["egress_acknowledged"],
        "draft_evidence_acknowledged": handoff_payload["draft_evidence_acknowledged"],
        "source_created_at": output_payload["created_at"],
        "imported_at": "2026-07-12T12:10:00Z",
        "agent": output_payload["agent"],
        "title": output_payload["title"],
        "content_sha256": output_payload["content_sha256"],
        "validation": {
            "schema_valid": validation.schema_valid,
            "package_integrity": validation.package_integrity,
            "dossier_current": validation.dossier_current,
            "citations_resolved": validation.citations_resolved,
            "coverage_complete": validation.coverage_complete,
            "unsupported_sections": unsupported_sections,
        },
        "human_reviewed": False,
        "warnings": list(validation.warnings),
        "files": {},
    }
    imported_package = SimpleNamespace(
        manifest=imported_manifest,
        validation=_json_dataclass(validation),
        markdown=output_payload["content_markdown"],
        files={},
    )

    def settings_call(args):
        calls["order"].append("settings")
        calls["settings"] = args
        return settings

    def output_root_call(output_root, *, generated_root, acknowledge_unsafe):
        calls["order"].append("validate_output_root")
        calls["validate_output_root"] = {
            "output_root": output_root,
            "generated_root": generated_root,
            "acknowledge_unsafe": acknowledge_unsafe,
        }
        if output_policy_error is not None:
            raise output_policy_error
        return location_warning

    def client_call(actual_settings):
        calls["order"].append("client")
        calls["client_settings"] = actual_settings
        return object()

    def repository_call(client):
        calls["order"].append("repository")
        calls["repository_client"] = client
        return repository

    def load_output_call(path):
        calls["order"].append("load_writing_output")
        calls["load_writing_output"] = path
        return writing_output

    def load_handoff_call(path):
        calls["order"].append("load_handoff")
        calls["load_handoff"] = path
        return handoff_package

    def load_dossier_call(path):
        calls["order"].append("load_dossier")
        calls["load_dossier"] = path
        return dossier_package

    def prepare_call(actual_repository, revision, handoff, output, *, validated_at):
        calls["order"].append("prepare_import")
        calls["prepare_import"] = {
            "repository": actual_repository,
            "revision": revision,
            "handoff": handoff,
            "output": output,
            "validated_at": validated_at,
        }
        if failure_code is not None:
            raise _SafeWritingImportFailure(failure_code, validation)
        return import_result

    def materialize_call(output, handoff, actual_validation, *, imported_at):
        calls["order"].append("materialize_import")
        calls["materialize_import"] = {
            "output": output,
            "handoff": handoff,
            "validation": actual_validation,
            "imported_at": imported_at,
        }
        return imported_package

    def publish_call(output_root, package):
        calls["order"].append("publish_import")
        path = output_root / package.manifest["dossier_key"] / "outputs" / package.manifest["writing_id"]
        calls["publish_import"] = (output_root, package)
        return SimpleNamespace(status=publication_status, path=path, package=package)

    monkeypatch.setattr(cli, "_settings", settings_call)
    monkeypatch.setattr(cli, "validate_output_root", output_root_call)
    monkeypatch.setattr(cli, "ArangoClient", client_call)
    monkeypatch.setattr(cli, "KnowledgeRepository", repository_call)
    monkeypatch.setattr(cli, "load_writing_output_package", load_output_call, raising=False)
    monkeypatch.setattr(cli, "load_writing_handoff", load_handoff_call, raising=False)
    monkeypatch.setattr(cli, "load_dossier_package", load_dossier_call)
    monkeypatch.setattr(cli, "prepare_writing_import", prepare_call, raising=False)
    monkeypatch.setattr(cli, "materialize_imported_writing_package", materialize_call, raising=False)
    monkeypatch.setattr(cli, "publish_imported_writing_package", publish_call, raising=False)
    monkeypatch.setattr(cli, "WritingImportError", _SafeWritingImportFailure, raising=False)
    return calls, settings


@pytest.mark.parametrize(
    ("output_kind", "publication_status"),
    [("draft", "created"), ("summary", "reused")],
)
def test_research_import_output_accepts_both_kinds_and_reimport_has_stable_success_json(
    capsys,
    monkeypatch,
    tmp_path,
    dossier_manifest_builder,
    handoff_package_builder,
    requested_output_builder,
    writing_output_package_builder,
    output_kind,
    publication_status,
) -> None:
    dossier_manifest = dossier_manifest_builder()
    handoff_payload = handoff_package_builder(
        dossier_manifest=dossier_manifest,
        requested_output=requested_output_builder(kind=output_kind),
    )
    output_payload = writing_output_package_builder(handoff=handoff_payload)
    validation = _writing_validation_result(
        "writing_output",
        output_payload["package_digest"],
        output_payload["package_digest"],
        status="valid_with_warnings",
        warnings=("structural_coverage_only",),
    )
    calls, settings = _install_research_import_seams(
        monkeypatch,
        tmp_path,
        dossier_manifest,
        handoff_payload,
        output_payload,
        validation,
        publication_status=publication_status,
        location_warning="output_outside_generated_zone",
    )
    incoming_path = tmp_path / f"incoming-{output_kind}.json"
    handoff_path = tmp_path / "trusted-handoff.json"
    output_root = tmp_path / "acknowledged-external-research"

    code = cli.main(
        [
            "research",
            "import-output",
            str(incoming_path),
            "--handoff",
            str(handoff_path),
            "--output-root",
            str(output_root),
            "--acknowledge-unsafe-output",
        ]
    )
    captured = capsys.readouterr()
    writing_id = "writing-0123456789abcdef"
    expected = {
        "status": "ok",
        "writing_id": writing_id,
        "output_kind": output_kind,
        "dossier_key": dossier_manifest["dossier_key"],
        "revision_id": dossier_manifest["revision_id"],
        "output": str(output_root / dossier_manifest["dossier_key"] / "outputs" / writing_id),
        "citations_resolved": True,
        "coverage_complete": True,
        "unsupported_sections": 0,
        "human_reviewed": False,
        "warnings": ["structural_coverage_only", "output_outside_generated_zone"],
    }

    assert code == 0
    assert captured.out == json.dumps(expected, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    assert "structural_coverage_only" in captured.err and "output_outside_generated_zone" in captured.err
    assert calls["validate_output_root"] == {
        "output_root": output_root,
        "generated_root": settings.repo_root / "data" / "generated",
        "acknowledge_unsafe": True,
    }
    assert calls["load_writing_output"] == incoming_path
    assert calls["load_handoff"] == handoff_path
    expected_revision_path = output_root / dossier_manifest["dossier_key"] / "revisions" / dossier_manifest["revision_id"]
    assert calls["load_dossier"] == expected_revision_path
    prepared = calls["prepare_import"]
    assert prepared["repository"] is not None
    assert prepared["revision"].revision_id == dossier_manifest["revision_id"]
    assert prepared["handoff"].handoff_id == handoff_payload["handoff_id"]
    assert prepared["output"].package_digest == output_payload["package_digest"]
    assert prepared["validated_at"].endswith("Z")
    assert calls["materialize_import"] == {
        "output": prepared["output"],
        "handoff": prepared["handoff"],
        "validation": validation,
        "imported_at": calls["materialize_import"]["imported_at"],
    }
    assert calls["materialize_import"]["imported_at"].endswith("Z")
    assert calls["publish_import"][0] == output_root


def test_research_import_output_rejects_whole_package_with_safe_validation_envelope(
    capsys,
    monkeypatch,
    tmp_path,
    dossier_manifest_builder,
    handoff_package_builder,
    writing_output_package_builder,
) -> None:
    dossier_manifest = dossier_manifest_builder()
    handoff_payload = handoff_package_builder(dossier_manifest=dossier_manifest)
    output_payload = writing_output_package_builder(handoff=handoff_payload)
    validation = _writing_validation_result(
        "writing_output",
        output_payload["package_digest"],
        output_payload["package_digest"],
        status="invalid",
        warnings=("output_package_rejected",),
    )
    calls, _ = _install_research_import_seams(
        monkeypatch,
        tmp_path,
        dossier_manifest,
        handoff_payload,
        output_payload,
        validation,
        failure_code="writing_output_invalid",
    )

    code = cli.main(
        [
            "research",
            "import-output",
            str(tmp_path / "untrusted-output.json"),
            "--handoff",
            str(tmp_path / "trusted-handoff.json"),
        ]
    )
    captured = capsys.readouterr()
    expected = {
        "status": "rejected",
        "reason": "writing_output_invalid",
        "validation": _json_dataclass(validation),
        "warnings": ["output_package_rejected"],
    }

    assert code == 1
    assert captured.out == json.dumps(expected, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    assert "output_package_rejected" in captured.err
    assert "private import diagnostic" not in captured.out
    assert "prepare_import" in calls
    assert not {"materialize_import", "publish_import"} & calls.keys()


def test_research_import_output_custom_root_policy_failure_precedes_untrusted_input_reads(
    capsys,
    monkeypatch,
    tmp_path,
    dossier_manifest_builder,
    handoff_package_builder,
    writing_output_package_builder,
) -> None:
    dossier_manifest = dossier_manifest_builder()
    handoff_payload = handoff_package_builder(dossier_manifest=dossier_manifest)
    output_payload = writing_output_package_builder(handoff=handoff_payload)
    validation = _writing_validation_result(
        "writing_output",
        output_payload["package_digest"],
        output_payload["package_digest"],
    )
    calls, _ = _install_research_import_seams(
        monkeypatch,
        tmp_path,
        dossier_manifest,
        handoff_payload,
        output_payload,
        validation,
        output_policy_error=OutputRootAcknowledgementRequired("custom output requires acknowledgement"),
    )

    code = cli.main(
        [
            "research",
            "import-output",
            str(tmp_path / "untrusted-output.json"),
            "--handoff",
            str(tmp_path / "trusted-handoff.json"),
            "--output-root",
            str(tmp_path / "unacknowledged-external-root"),
        ]
    )
    payload, _ = _read_cli_output(capsys)

    assert code == 1 and payload["error_type"] == "OutputRootAcknowledgementRequired"
    assert calls["order"] == ["settings", "validate_output_root"]
    assert (
        not {
            "load_writing_output",
            "load_handoff",
            "load_dossier",
            "client",
            "repository",
            "prepare_import",
            "materialize_import",
            "publish_import",
        }
        & calls.keys()
    )


@pytest.mark.parametrize(
    ("artifact_kind", "validation_status", "expected_exit"),
    [
        ("dossier", "valid", 0),
        ("handoff", "valid_with_warnings", 0),
        ("writing_output", "invalid", 1),
        ("imported_writing", "valid", 0),
    ],
)
def test_research_validate_dispatches_every_artifact_type_and_resolves_links_from_output_root(
    capsys,
    monkeypatch,
    tmp_path,
    dossier_manifest_builder,
    handoff_package_builder,
    writing_output_package_builder,
    artifact_kind,
    validation_status,
    expected_exit,
) -> None:
    calls: dict[str, Any] = {
        "load_dossier": [],
        "load_handoff": [],
        "load_writing_output": [],
        "load_imported": [],
        "validators": [],
    }
    settings = SimpleNamespace(repo_root=tmp_path / "repository")
    repository = object()
    dossier_manifest = dossier_manifest_builder()
    handoff_payload = handoff_package_builder(dossier_manifest=dossier_manifest)
    output_payload = writing_output_package_builder(
        handoff=handoff_payload,
        content_markdown="Quoted data only: file:///private/never-open and https://example.invalid/never-fetch",
        dossier_key="research-untrusted-envelope-ffffffffffff",
        revision_id="rev-20260712T130000Z-deadbeef",
        agent={
            "name": "synthetic-agent",
            "model": "synthetic-model",
            "run_id": "../../private/never-open",
        },
    )
    imported_manifest = {
        "artifact_type": "imported_writing",
        "writing_id": "writing-0123456789abcdef",
        "incoming_package_digest": output_payload["package_digest"],
        "handoff_id": handoff_payload["handoff_id"],
        "handoff_digest": handoff_payload["package_digest"],
        "dossier_key": dossier_manifest["dossier_key"],
        "revision_id": dossier_manifest["revision_id"],
        "revision_content_digest": dossier_manifest["content_digest"],
    }
    dossier_package = SimpleNamespace(manifest=dossier_manifest, validation={}, markdown="", files={})
    handoff_package = SimpleNamespace(**handoff_payload)
    writing_output = SimpleNamespace(**output_payload)
    imported_package = SimpleNamespace(
        manifest=imported_manifest,
        validation={},
        markdown="Quoted data: file:///private/never-open",
        files={},
    )

    output_root = tmp_path / "research-root"
    dossier_path = output_root / dossier_manifest["dossier_key"] / "revisions" / dossier_manifest["revision_id"]
    resolved_handoff_path = output_root / dossier_manifest["dossier_key"] / "handoffs" / f"{handoff_payload['handoff_id']}.json"
    explicit_handoff_path = tmp_path / "explicit-local-handoff.json"
    explicit_handoff_path.write_text(json.dumps(handoff_payload), encoding="utf-8")

    if artifact_kind == "dossier":
        artifact = tmp_path / "dossier-artifact"
        artifact.mkdir()
        (artifact / "manifest.json").write_text(json.dumps(dossier_manifest), encoding="utf-8")
        target_type = "dossier_revision"
        target_id = dossier_manifest["revision_id"]
        target_digest = dossier_manifest["content_digest"]
    elif artifact_kind == "handoff":
        artifact = tmp_path / "handoff-artifact.json"
        artifact.write_text(json.dumps(handoff_payload), encoding="utf-8")
        target_type = "writing_handoff"
        target_id = handoff_payload["handoff_id"]
        target_digest = handoff_payload["package_digest"]
    elif artifact_kind == "writing_output":
        artifact = tmp_path / "incoming-writing-output.json"
        artifact.write_text(json.dumps(output_payload), encoding="utf-8")
        target_type = "writing_output"
        target_id = output_payload["package_digest"]
        target_digest = output_payload["package_digest"]
    else:
        artifact = tmp_path / "imported-writing-artifact"
        artifact.mkdir()
        (artifact / "manifest.json").write_text(json.dumps(imported_manifest), encoding="utf-8")
        target_type = "imported_writing"
        target_id = imported_manifest["writing_id"]
        target_digest = imported_manifest["incoming_package_digest"]

    warnings = ("synthetic_validate_warning",) if validation_status == "valid_with_warnings" else ()
    validation = _writing_validation_result(
        target_type,
        target_id,
        target_digest,
        status=validation_status,
        warnings=warnings,
    )

    def settings_call(args):
        calls["settings"] = args
        return settings

    def client_call(actual_settings):
        calls["client_settings"] = actual_settings
        return object()

    def repository_call(client):
        calls["repository_client"] = client
        return repository

    def load_dossier_call(path):
        calls["load_dossier"].append(path)
        return dossier_package

    def load_handoff_call(path):
        calls["load_handoff"].append(path)
        return handoff_package

    def load_output_call(path):
        calls["load_writing_output"].append(path)
        return writing_output

    def load_imported_call(path):
        calls["load_imported"].append(path)
        return imported_package

    def dossier_validator(actual_repository, revision, *, validated_at):
        calls["validators"].append(("dossier", actual_repository, revision, validated_at))
        return validation

    def handoff_validator(actual_repository, revision, handoff, *, validated_at):
        calls["validators"].append(("handoff", actual_repository, revision, handoff, validated_at))
        return validation

    def output_validator(actual_repository, revision, handoff, output, *, validated_at):
        calls["validators"].append(("writing_output", actual_repository, revision, handoff, output, validated_at))
        return validation

    def imported_validator(actual_repository, revision, handoff, package, *, validated_at):
        calls["validators"].append(("imported_writing", actual_repository, revision, handoff, package, validated_at))
        return validation

    monkeypatch.setattr(cli, "_settings", settings_call)
    monkeypatch.setattr(cli, "ArangoClient", client_call)
    monkeypatch.setattr(cli, "KnowledgeRepository", repository_call)
    monkeypatch.setattr(cli, "load_dossier_package", load_dossier_call)
    monkeypatch.setattr(cli, "load_writing_handoff", load_handoff_call, raising=False)
    monkeypatch.setattr(cli, "load_writing_output_package", load_output_call, raising=False)
    monkeypatch.setattr(cli, "load_imported_writing_package", load_imported_call, raising=False)
    monkeypatch.setattr(cli, "validate_dossier_revision", dossier_validator)
    monkeypatch.setattr(cli, "validate_writing_handoff", handoff_validator, raising=False)
    monkeypatch.setattr(cli, "validate_writing_output_package", output_validator, raising=False)
    monkeypatch.setattr(cli, "validate_imported_writing_package", imported_validator, raising=False)

    arguments = ["research", "validate", str(artifact), "--output-root", str(output_root)]
    if artifact_kind == "writing_output":
        arguments.extend(["--handoff", str(explicit_handoff_path)])
    code = cli.main(arguments)
    captured = capsys.readouterr()

    assert code == expected_exit
    assert captured.out == json.dumps(_json_dataclass(validation), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    for warning in warnings:
        assert warning in captured.err
    if not warnings:
        assert captured.err == ""
    assert calls["validators"][0][0] == artifact_kind
    assert calls["validators"][0][1] is repository
    assert calls["validators"][0][-1].endswith("Z")

    if artifact_kind == "dossier":
        assert calls["load_dossier"] == [artifact]
        assert calls["load_handoff"] == calls["load_writing_output"] == calls["load_imported"] == []
    else:
        assert calls["load_dossier"] == [dossier_path]
    if artifact_kind == "handoff":
        assert calls["load_handoff"] == [artifact]
    elif artifact_kind == "writing_output":
        assert calls["load_writing_output"] == [artifact]
        assert calls["load_handoff"] == [explicit_handoff_path]
    elif artifact_kind == "imported_writing":
        assert calls["load_imported"] == [artifact]
        assert calls["load_handoff"] == [resolved_handoff_path]

    all_loaded_paths = [
        *calls["load_dossier"],
        *calls["load_handoff"],
        *calls["load_writing_output"],
        *calls["load_imported"],
    ]
    assert all("never-open" not in str(path) and "never-fetch" not in str(path) for path in all_loaded_paths)


def test_research_validate_writing_output_requires_explicit_handoff_before_related_artifact_access(
    capsys,
    monkeypatch,
    tmp_path,
    handoff_package_builder,
    writing_output_package_builder,
) -> None:
    handoff_payload = handoff_package_builder()
    output_payload = writing_output_package_builder(handoff=handoff_payload)
    artifact = tmp_path / "incoming-writing-output.json"
    artifact.write_text(json.dumps(output_payload), encoding="utf-8")
    calls: list[tuple[str, Any]] = []

    def load_output_call(path):
        calls.append(("load_output", path))
        return SimpleNamespace(**output_payload)

    def forbidden(*args, **kwargs):
        calls.append(("forbidden_related_access", args[0] if args else None))
        raise RuntimeError("related artifact access happened before the required --handoff gate")

    monkeypatch.setattr(cli, "load_writing_output_package", load_output_call, raising=False)
    monkeypatch.setattr(cli, "load_writing_handoff", forbidden, raising=False)
    monkeypatch.setattr(cli, "load_dossier_package", forbidden)
    monkeypatch.setattr(cli, "validate_writing_output_package", forbidden, raising=False)
    monkeypatch.setattr(cli, "ArangoClient", forbidden)
    monkeypatch.setattr(cli, "KnowledgeRepository", forbidden)

    code = cli.main(["research", "validate", str(artifact)])
    payload, stderr = _read_cli_output(capsys)

    assert code == 1 and payload["status"] == "error"
    assert "handoff" in payload["error"].lower()
    assert "usage:" not in stderr.lower()
    assert calls in ([], [("load_output", artifact)])
