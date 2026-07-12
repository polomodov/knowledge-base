from __future__ import annotations

import builtins
import hashlib
import importlib
import json
import os
import stat
import sys
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest

import knowledge_base.research_artifacts as research_artifacts
import knowledge_base.research_workflow as research_workflow
from knowledge_base.research_artifacts import (
    ArtifactCollisionError,
    OutputRootAcknowledgementRequired,
    UnsafeArtifactPathError,
    canonical_sha256,
)
from knowledge_base.research_workflow import DossierRevision, DossierValidationError, ValidationResult

JsonObject = dict[str, Any]
Builder = Callable[..., JsonObject]

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = ROOT / "specs" / "007-writer-research-workflow" / "contracts"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "research"
MAX_WRITING_PACKAGE_BYTES = 2 * 1024 * 1024
VALIDATED_AT = "2026-07-12T16:00:00Z"
CREATED_AT = "2026-07-12T16:01:00Z"
PRIVATE_MARKER = "private-settings-value-must-not-leak"


def _writing_handoff() -> ModuleType:
    return importlib.import_module("knowledge_base.writing_handoff")


def _api(name: str) -> Any:
    return getattr(_writing_handoff(), name)


def _json_bytes(value: Mapping[str, Any], *, sort_keys: bool = False) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=sort_keys,
    ).encode("utf-8")


def _schemas() -> dict[str, JsonObject]:
    return {
        path.name: cast(JsonObject, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(CONTRACT_DIR.glob("*.schema.json"))
    }


def _schema_validator(name: str) -> Any:
    from jsonschema import Draft202012Validator, FormatChecker
    from referencing import Registry, Resource

    schemas = _schemas()
    registry = Registry().with_resources((schema["$id"], Resource.from_contents(schema)) for schema in schemas.values())
    return Draft202012Validator(
        schemas[name],
        registry=registry,
        format_checker=FormatChecker(),
    )


@pytest.mark.parametrize(
    "fixture_name",
    [
        "valid-writing-output-draft.json",
        "valid-writing-output-summary.json",
        "invalid-writing-output.json",
    ],
)
def test_writing_output_fixtures_match_schema_contract(fixture_name: str) -> None:
    payload = json.loads((FIXTURE_DIR / fixture_name).read_text(encoding="utf-8"))

    _schema_validator("writing-output-package.schema.json").validate(payload)


def test_invalid_writing_output_fixture_is_schema_valid_but_uses_unknown_citation_schema_contract() -> None:
    valid = json.loads((FIXTURE_DIR / "valid-writing-output-draft.json").read_text(encoding="utf-8"))
    invalid = json.loads((FIXTURE_DIR / "invalid-writing-output.json").read_text(encoding="utf-8"))

    _schema_validator("writing-output-package.schema.json").validate(invalid)
    assert invalid["sections"][0]["citation_ids"] != valid["sections"][0]["citation_ids"]
    assert invalid["sections"][0]["citation_ids"] == ["cit-deadbeefdeadbeef"]


@pytest.mark.parametrize("includes_drafts", [False, True])
def test_handoff_builder_matches_schema_contract(
    dossier_manifest_builder: Builder,
    handoff_package_builder: Builder,
    includes_drafts: bool,
) -> None:
    visibility = "published_and_drafts" if includes_drafts else "published_only"
    manifest = dossier_manifest_builder(
        request={
            **dossier_manifest_builder()["request"],
            "visibility": visibility,
        },
    )
    payload = handoff_package_builder(dossier_manifest=manifest)

    _schema_validator("handoff-package.schema.json").validate(payload)
    assert payload["draft_evidence_acknowledged"] is includes_drafts


def test_parse_writing_output_accepts_both_fixture_kinds_and_preserves_unicode() -> None:
    parse = _api("parse_writing_output_package")
    package_type = _api("WritingOutputPackage")

    draft = parse((FIXTURE_DIR / "valid-writing-output-draft.json").read_bytes())
    summary = parse((FIXTURE_DIR / "valid-writing-output-summary.json").read_bytes())

    assert isinstance(draft, package_type)
    assert isinstance(summary, package_type)
    assert draft.output_kind == "draft"
    assert summary.output_kind == "summary"
    assert "🧭" in draft.content_markdown
    assert "🧭" in summary.content_markdown


def test_parse_writing_output_enforces_exact_two_mib_byte_boundary(
    writing_output_package_builder: Builder,
) -> None:
    parse = _api("parse_writing_output_package")
    contract_error = _api("WritingOutputContractError")
    encoded = _json_bytes(writing_output_package_builder())
    at_limit = encoded + b" " * (MAX_WRITING_PACKAGE_BYTES - len(encoded))

    assert parse(at_limit).output_kind == "draft"
    with pytest.raises(contract_error):
        parse(at_limit + b" ")


def _mutate_output_contract(payload: JsonObject, case: str) -> JsonObject:
    changed = deepcopy(payload)
    if case == "version":
        changed["schema_version"] = "2.0"
    elif case == "artifact_type":
        changed["artifact_type"] = "dossier_revision"
    elif case == "unknown_root":
        changed["provider_api_key"] = PRIVATE_MARKER
    elif case == "unknown_agent":
        changed["agent"]["endpoint"] = "file:///private/agent.sock"
    elif case == "unknown_section":
        changed["sections"][0]["read_from"] = "/private/archive.json"
    elif case == "control_character":
        changed["title"] = "unsafe\u0000title"
    elif case == "title_bound":
        changed["title"] = "x" * 501
    elif case == "agent_bound":
        changed["agent"]["model"] = "x" * 501
    elif case == "section_bound":
        changed["sections"] = [deepcopy(changed["sections"][0]) for _ in range(201)]
    elif case == "citation_bound":
        changed["sections"][0]["citation_ids"] = [f"cit-{index:016x}" for index in range(51)]
    elif case == "invalid_timestamp":
        changed["created_at"] = "2026-07-12 16:00:00"
    elif case == "bool_offset":
        changed["sections"][0]["char_start"] = False
    else:  # pragma: no cover - test table controls cases
        raise AssertionError(case)
    return changed


@pytest.mark.parametrize(
    "case",
    [
        "version",
        "artifact_type",
        "unknown_root",
        "unknown_agent",
        "unknown_section",
        "control_character",
        "title_bound",
        "agent_bound",
        "section_bound",
        "citation_bound",
        "invalid_timestamp",
        "bool_offset",
    ],
)
def test_parse_writing_output_rejects_strict_contract_violations(
    writing_output_package_builder: Builder,
    case: str,
) -> None:
    parse = _api("parse_writing_output_package")
    contract_error = _api("WritingOutputContractError")
    payload = _mutate_output_contract(writing_output_package_builder(), case)

    with pytest.raises(contract_error):
        parse(_json_bytes(payload))


def test_parse_writing_output_rejects_duplicate_json_members(
    writing_output_package_builder: Builder,
) -> None:
    parse = _api("parse_writing_output_package")
    contract_error = _api("WritingOutputContractError")
    encoded = _json_bytes(writing_output_package_builder())
    duplicated = encoded.replace(
        b'{"schema_version":"1.0",',
        b'{"schema_version":"1.0","schema_version":"1.0",',
        1,
    )
    assert duplicated != encoded

    with pytest.raises(contract_error):
        parse(duplicated)


def test_load_writing_output_reads_one_bounded_regular_file(
    tmp_path: Path,
    writing_output_package_builder: Builder,
) -> None:
    load = _api("load_writing_output_package")
    package_type = _api("WritingOutputPackage")
    path = tmp_path / "writing-output.json"
    path.write_bytes(_json_bytes(writing_output_package_builder()))

    loaded = load(path)

    assert isinstance(loaded, package_type)
    assert loaded.output_kind == "draft"


def test_runtime_parser_does_not_import_jsonschema(
    monkeypatch: pytest.MonkeyPatch,
    writing_output_package_builder: Builder,
) -> None:
    module = _writing_handoff()
    real_import = builtins.__import__

    def reject_jsonschema(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.partition(".")[0] == "jsonschema":
            raise AssertionError("runtime writing_handoff must remain stdlib-only")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_jsonschema)
    reloaded = importlib.reload(module)

    assert reloaded.parse_writing_output_package(_json_bytes(writing_output_package_builder())).output_kind == "draft"


def _citation(citation_builder: Builder, name: str, *, status: str = "published") -> JsonObject:
    excerpt = f"Synthetic evidence for {name} 🧭."
    return citation_builder(
        canonical_id=f"canonical-{name}",
        document_key=f"doc-{name}",
        chunk_key=f"chunk-{name}-0",
        char_start=0,
        char_end=len(excerpt),
        excerpt=excerpt,
        title=f"Synthetic {name}",
        document_status=status,
        url=f"https://example.test/{name}",
        raw_snapshot_key=f"raw-{name}",
        import_run_key=f"import-{name}",
    )


def _revision(
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
    *,
    visibility: str = "published_only",
    states: Sequence[str] = ("pinned", "selected", "excluded"),
    warnings: Sequence[str] = (),
    private_context: bool = False,
) -> DossierRevision:
    citations = [
        _citation(
            citation_builder,
            f"{state}-{index}",
            status="draft" if visibility == "published_and_drafts" and index == 1 else "published",
        )
        for index, state in enumerate(states, start=1)
    ]
    candidates = [
        evidence_candidate_builder(
            citation=citation,
            document_rank=index,
            fragment_rank=1,
            selection_state=state,
            selection_reason=f"fixture-{state}",
        )
        for index, (citation, state) in enumerate(zip(citations, states, strict=True), start=1)
    ]
    request = dossier_manifest_builder()["request"]
    request["visibility"] = visibility
    context = dossier_manifest_builder()["corpus_context"]
    derived_context: JsonObject = {"topics": [], "leads": []}
    if private_context:
        context["database"] = PRIVATE_MARKER
        derived_context["leads"] = [{"kind": "clean_community", "summary": PRIVATE_MARKER}]
    manifest = dossier_manifest_builder(
        request=request,
        candidate_evidence=candidates,
        corpus_context=context,
        derived_context=derived_context,
        status="degraded" if warnings else "ready",
        warnings=list(warnings),
    )
    return DossierRevision(**manifest)


def _validation(
    revision: DossierRevision,
    *,
    status: str = "valid",
    target_type: str = "dossier_revision",
    target_id: str | None = None,
    target_digest: str | None = None,
    warnings: Sequence[str] = (),
    errors: Sequence[str] = (),
    validated_at: str = VALIDATED_AT,
) -> ValidationResult:
    current = status != "invalid"
    citations = tuple(
        {
            "citation_id": citation_id,
            "status": "valid" if current else "changed",
            "reason": None if current else "synthetic current evidence mismatch",
        }
        for citation_id in revision.selected_citation_ids
    )
    effective_warnings = tuple(warnings)
    if status == "valid_with_warnings" and not effective_warnings:
        effective_warnings = ("synthetic_validation_warning",)
    effective_errors = tuple(errors)
    if status == "invalid" and not effective_errors:
        effective_errors = ("synthetic current dossier rejection",)
    return ValidationResult(
        schema_version="1.0",
        artifact_type="validation_result",
        target_type=target_type,
        target_id=target_id or revision.revision_id,
        target_digest=target_digest or revision.content_digest,
        status=status,
        schema_valid=True,
        package_integrity=current,
        dossier_current=current,
        citations_resolved=current,
        coverage_complete=current,
        human_reviewed=False,
        citations=citations,
        warnings=effective_warnings,
        errors=effective_errors,
        validated_at=validated_at,
    )


def _patch_dossier_validation(
    monkeypatch: pytest.MonkeyPatch,
    revision: DossierRevision,
    *,
    status: str = "valid",
) -> list[str]:
    calls: list[str] = []

    def validate(
        repository: object,
        candidate_revision: DossierRevision,
        *,
        validated_at: str,
    ) -> ValidationResult:
        del repository
        assert candidate_revision is revision
        calls.append(validated_at)
        return _validation(revision, status=status, validated_at=validated_at)

    monkeypatch.setattr(research_workflow, "validate_dossier_revision", validate)
    monkeypatch.setattr(_writing_handoff(), "validate_dossier_revision", validate, raising=False)
    return calls


def _requested_output(kind: str = "draft") -> Any:
    return _api("RequestedWritingOutput")(
        kind=kind,
        language="ru",
        style="analytical and citation-aware",
        max_words=800,
    )


def _build_handoff(
    monkeypatch: pytest.MonkeyPatch,
    revision: DossierRevision,
    *,
    kind: str = "draft",
    egress_acknowledged: bool = True,
    allow_draft_evidence: bool = False,
    created_at: str = CREATED_AT,
    repository: object | None = None,
) -> Any:
    _patch_dossier_validation(monkeypatch, revision)
    return _api("build_writing_handoff")(
        repository or SimpleNamespace(client=SimpleNamespace(settings=SimpleNamespace(arango_password=PRIVATE_MARKER))),
        revision,
        _requested_output(kind),
        egress_acknowledged=egress_acknowledged,
        allow_draft_evidence=allow_draft_evidence,
        validated_at=VALIDATED_AT,
        created_at=created_at,
    )


def test_parse_handoff_is_strict_about_nested_allowlists_and_control_characters(
    handoff_package_builder: Builder,
) -> None:
    parse = _api("parse_handoff_package")
    handoff_error = _api("WritingHandoffError")
    mutations = []
    for case in ("root", "requested", "evidence", "control", "allowlist"):
        payload = handoff_package_builder()
        if case == "root":
            payload["credentials"] = {"token": PRIVATE_MARKER}
        elif case == "requested":
            payload["requested_output"]["provider"] = "external"
        elif case == "evidence":
            payload["evidence"][0]["raw_payload"] = PRIVATE_MARKER
        elif case == "control":
            payload["instructions"][0] = "unsafe\u0007instruction"
        else:
            payload["citation_allowlist"] = [payload["citation_allowlist"][0]] * 2
        mutations.append(payload)

    for payload in mutations:
        with pytest.raises(handoff_error):
            parse(_json_bytes(payload))


def test_parse_handoff_accepts_valid_package_and_enforces_two_mib_boundary(
    handoff_package_builder: Builder,
) -> None:
    parse = _api("parse_handoff_package")
    package_type = _api("HandoffPackage")
    handoff_error = _api("WritingHandoffError")
    encoded = _json_bytes(handoff_package_builder())
    at_limit = encoded + b" " * (MAX_WRITING_PACKAGE_BYTES - len(encoded))

    parsed = parse(at_limit)

    assert isinstance(parsed, package_type)
    assert parsed.artifact_type == "writing_handoff"
    with pytest.raises(handoff_error):
        parse(at_limit + b" ")


def _mutate_handoff_contract(payload: JsonObject, case: str) -> bytes:
    changed = deepcopy(payload)
    if case == "version":
        changed["schema_version"] = "2.0"
    elif case == "duplicate":
        encoded = _json_bytes(changed)
        duplicated = encoded.replace(
            b'{"schema_version":"1.0",',
            b'{"schema_version":"1.0","schema_version":"1.0",',
            1,
        )
        assert duplicated != encoded
        return duplicated
    elif case == "query_bound":
        changed["query"] = "я" * 1001
    elif case == "language_bound":
        changed["requested_output"]["language"] = "r"
    elif case == "style_bound":
        changed["requested_output"]["style"] = "x" * 1001
    elif case == "max_words_bound":
        changed["requested_output"]["max_words"] = 49
    elif case == "instruction_count":
        changed["instructions"] = ["bounded instruction"] * 51
    elif case == "instruction_bound":
        changed["instructions"] = ["x" * 4001]
    elif case == "warning_count":
        changed["warnings"] = [f"warning-{index}" for index in range(101)]
    else:  # pragma: no cover - test table controls cases
        raise AssertionError(case)
    return _json_bytes(changed)


@pytest.mark.parametrize(
    "case",
    [
        "version",
        "duplicate",
        "query_bound",
        "language_bound",
        "style_bound",
        "max_words_bound",
        "instruction_count",
        "instruction_bound",
        "warning_count",
    ],
)
def test_parse_handoff_rejects_versions_duplicates_and_bounds(
    handoff_package_builder: Builder,
    case: str,
) -> None:
    parse = _api("parse_handoff_package")
    handoff_error = _api("WritingHandoffError")

    with pytest.raises(handoff_error):
        parse(_mutate_handoff_contract(handoff_package_builder(), case))


def test_build_handoff_uses_non_circular_digests_and_semantic_identity_ignores_created_at(
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
) -> None:
    revision = _revision(dossier_manifest_builder, evidence_candidate_builder, citation_builder)
    first = _build_handoff(monkeypatch, revision, created_at="2026-07-12T16:01:00Z")
    repeated = _build_handoff(monkeypatch, revision, created_at="2026-07-13T08:00:00Z")
    first_payload = asdict(first)
    identity_projection = {
        key: value
        for key, value in first_payload.items()
        if key not in {"created_at", "handoff_id", "identity_sha256", "package_digest"}
    }
    package_projection = {key: value for key, value in first_payload.items() if key not in {"created_at", "package_digest"}}

    assert isinstance(first, _api("HandoffPackage"))
    assert first.identity_sha256 == canonical_sha256(identity_projection)
    assert first.handoff_id == f"handoff-{first.identity_sha256[:16]}"
    assert first.package_digest == canonical_sha256(package_projection)
    assert repeated.handoff_id == first.handoff_id
    assert repeated.identity_sha256 == first.identity_sha256
    assert repeated.package_digest == first.package_digest
    assert repeated.created_at != first.created_at


def test_handoff_projects_exact_pinned_then_selected_evidence_and_privacy_boundary(
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
) -> None:
    revision = _revision(
        dossier_manifest_builder,
        evidence_candidate_builder,
        citation_builder,
        states=("selected", "pinned", "excluded"),
        warnings=("synthetic_dossier_warning",),
        private_context=True,
    )
    handoff = _build_handoff(monkeypatch, revision)
    payload = asdict(handoff)
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    expected_ids = tuple(revision.selected_citation_ids)

    assert tuple(citation["citation_id"] for citation in payload["evidence"]) == expected_ids
    assert tuple(payload["citation_allowlist"]) == expected_ids
    excluded_ids = {
        candidate.citation.citation_id for candidate in revision.candidate_evidence if candidate.selection_state == "excluded"
    }
    assert excluded_ids.isdisjoint(expected_ids)
    assert "quoted" in " ".join(payload["instructions"]).lower()
    assert "untrusted" in " ".join(payload["instructions"]).lower()
    assert "execute" in " ".join(payload["instructions"]).lower()
    assert "synthetic_dossier_warning" in payload["warnings"]
    assert any("sensitive" in warning.lower() or "owner_review" in warning.lower() for warning in payload["warnings"])
    assert PRIVATE_MARKER not in serialized
    assert "corpus_context" not in payload
    assert "derived_context" not in payload
    assert "raw_payload" not in serialized
    assert "file:///" not in serialized


@pytest.mark.parametrize(
    ("visibility", "egress", "draft_ack", "expected_code"),
    [
        ("published_only", False, False, "external_disclosure_not_acknowledged"),
        ("published_and_drafts", False, True, "external_disclosure_not_acknowledged"),
        ("published_and_drafts", True, False, "draft_evidence_not_acknowledged"),
    ],
)
def test_every_handoff_requires_egress_ack_and_drafts_require_second_ack(
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
    visibility: str,
    egress: bool,
    draft_ack: bool,
    expected_code: str,
) -> None:
    revision = _revision(
        dossier_manifest_builder,
        evidence_candidate_builder,
        citation_builder,
        visibility=visibility,
    )
    error_type = _api("WritingHandoffError")

    with pytest.raises(error_type) as raised:
        _build_handoff(
            monkeypatch,
            revision,
            egress_acknowledged=egress,
            allow_draft_evidence=draft_ack,
        )

    assert raised.value.code == expected_code
    assert raised.value.validation is None


def test_acknowledged_draft_handoff_records_both_decisions(
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
) -> None:
    revision = _revision(
        dossier_manifest_builder,
        evidence_candidate_builder,
        citation_builder,
        visibility="published_and_drafts",
    )

    handoff = _build_handoff(monkeypatch, revision, allow_draft_evidence=True)

    assert handoff.includes_drafts is True
    assert handoff.visibility == "published_and_drafts"
    assert handoff.egress_acknowledged is True
    assert handoff.draft_evidence_acknowledged is True


def _oversized_handoff_evidence(citation_builder: Builder) -> list[JsonObject]:
    citations: list[JsonObject] = []
    for index in range(100):
        prefix = f"Synthetic oversized evidence {index:03d}: "
        excerpt = prefix + "я" * (20_000 - len(prefix))
        citations.append(
            citation_builder(
                canonical_id=f"oversized-{index}",
                document_key=f"doc-oversized-{index}",
                chunk_key=f"chunk-oversized-{index}-0",
                char_start=0,
                char_end=len(excerpt),
                excerpt=excerpt,
                title=f"Oversized evidence {index}",
                url=f"https://example.test/oversized/{index}",
                raw_snapshot_key=f"raw-oversized-{index}",
                import_run_key=f"import-oversized-{index}",
            )
        )
    return citations


def test_build_and_publish_reject_handoff_that_cannot_be_loaded_under_two_mib_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
    handoff_package_builder: Builder,
) -> None:
    citations = _oversized_handoff_evidence(citation_builder)
    candidates = [
        evidence_candidate_builder(
            citation=citation,
            document_rank=(index % 50) + 1,
            fragment_rank=(index // 50) + 1,
            selection_state="selected",
            selection_reason="oversized-contract-test",
        )
        for index, citation in enumerate(citations)
    ]
    request = dossier_manifest_builder()["request"]
    request.update(
        document_limit=50,
        fragments_per_document=5,
        evidence_limit=100,
        candidate_limit=150,
    )
    revision = DossierRevision(**dossier_manifest_builder(request=request, candidate_evidence=candidates))

    with pytest.raises(_api("WritingHandoffError")) as raised:
        _build_handoff(monkeypatch, revision)
    assert raised.value.code == "handoff_too_large"

    payload = handoff_package_builder(evidence=citations)
    package = _api("HandoffPackage")(**payload)
    assert len((json.dumps(asdict(package), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()) > (
        MAX_WRITING_PACKAGE_BYTES
    )
    generated_root = tmp_path / "data" / "generated"
    generated_root.mkdir(parents=True)
    output_root = generated_root / "research"
    with pytest.raises(_api("WritingHandoffError")) as publish_error:
        _api("publish_writing_handoff")(
            output_root,
            package,
            generated_root=generated_root,
            acknowledge_unsafe=False,
        )
    assert publish_error.value.code == "handoff_too_large"
    target = output_root / package.dossier_key / "handoffs" / f"{package.handoff_id}.json"
    assert not target.exists()


def test_build_and_validate_handoff_require_a_current_dossier_gate(
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
    handoff_package_builder: Builder,
) -> None:
    revision = _revision(dossier_manifest_builder, evidence_candidate_builder, citation_builder)
    invalid = _validation(revision, status="invalid")

    def reject_current(*args: Any, **kwargs: Any) -> ValidationResult:
        return invalid

    monkeypatch.setattr(research_workflow, "validate_dossier_revision", reject_current)
    monkeypatch.setattr(_writing_handoff(), "validate_dossier_revision", reject_current, raising=False)
    error_type = _api("WritingHandoffError")
    with pytest.raises(error_type) as raised:
        _api("build_writing_handoff")(
            object(),
            revision,
            _requested_output(),
            egress_acknowledged=True,
            allow_draft_evidence=False,
            validated_at=VALIDATED_AT,
            created_at=CREATED_AT,
        )
    assert raised.value.code == "dossier_not_current"
    assert raised.value.validation is invalid

    parsed = _api("parse_handoff_package")(_json_bytes(handoff_package_builder(dossier_manifest=asdict(revision))))
    validation = _api("validate_writing_handoff")(
        object(),
        revision,
        parsed,
        validated_at=VALIDATED_AT,
    )
    assert validation.status == "invalid"
    assert validation.dossier_current is False
    assert validation.target_type == "writing_handoff"
    assert validation.target_id == parsed.handoff_id


@pytest.mark.parametrize(
    ("case", "overrides"),
    [
        ("identity", {"identity_sha256": "0" * 64}),
        ("package_digest", {"package_digest": "0" * 64}),
        ("revision_digest", {"revision_content_digest": "0" * 64}),
        ("revision_id", {"revision_id": "rev-20260713T000000Z-89abcdef"}),
        ("allowlist", {"citation_allowlist": ["cit-deadbeefdeadbeef"]}),
    ],
)
def test_validate_handoff_rejects_identity_integrity_and_dossier_mismatches(
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
    handoff_package_builder: Builder,
    case: str,
    overrides: Mapping[str, Any],
) -> None:
    revision = _revision(dossier_manifest_builder, evidence_candidate_builder, citation_builder)
    payload = handoff_package_builder(dossier_manifest=asdict(revision), **dict(overrides))
    parsed = _api("parse_handoff_package")(_json_bytes(payload))
    _patch_dossier_validation(monkeypatch, revision)

    validation = _api("validate_writing_handoff")(
        object(),
        revision,
        parsed,
        validated_at=VALIDATED_AT,
    )

    assert validation.status == "invalid", case
    assert validation.errors
    assert validation.target_type == "writing_handoff"
    assert validation.target_id == parsed.handoff_id


@pytest.mark.skipif(os.name != "posix", reason="V5 owner-only modes target POSIX")
def test_publish_handoff_is_owner_only_and_semantically_reuses_created_at_variant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
) -> None:
    revision = _revision(dossier_manifest_builder, evidence_candidate_builder, citation_builder)
    first = _build_handoff(monkeypatch, revision, created_at="2026-07-12T16:01:00Z")
    repeated = _build_handoff(monkeypatch, revision, created_at="2026-07-13T08:00:00Z")
    generated_root = tmp_path / "repo" / "data" / "generated"
    generated_root.mkdir(parents=True)
    output_root = generated_root / "research"

    created = _api("publish_writing_handoff")(
        output_root,
        first,
        generated_root=generated_root,
        acknowledge_unsafe=False,
    )

    def forbid_path_chmod(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise AssertionError("semantic reuse must enforce modes through verified descriptors")

    monkeypatch.setattr(_writing_handoff().os, "chmod", forbid_path_chmod)
    reused = _api("publish_writing_handoff")(
        output_root,
        repeated,
        generated_root=generated_root,
        acknowledge_unsafe=False,
    )

    expected_path = output_root / revision.dossier_key / "handoffs" / f"{first.handoff_id}.json"
    assert isinstance(created, _api("HandoffPublication"))
    assert created.status == "created"
    assert reused.status == "reused"
    assert created.path == expected_path
    assert reused.path == expected_path
    assert created.location_warning is None and reused.location_warning is None
    assert reused.package == created.package == first
    assert stat.S_IMODE(expected_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(expected_path.parent.stat().st_mode) == 0o700
    assert _api("load_writing_handoff")(expected_path) == first
    assert not any(path.name.endswith(".tmp") for path in expected_path.parent.iterdir())


@pytest.mark.skipif(os.name != "posix", reason="atomic collision recovery targets POSIX")
@pytest.mark.parametrize("semantic_match", [True, False], ids=["semantic-reuse", "true-collision"])
def test_publish_handoff_recovers_only_semantically_identical_collision_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
    semantic_match: bool,
) -> None:
    revision = _revision(dossier_manifest_builder, evidence_candidate_builder, citation_builder)
    requested = _build_handoff(monkeypatch, revision, created_at="2026-07-13T08:00:00Z")
    raced = _build_handoff(
        monkeypatch,
        revision,
        kind="draft" if semantic_match else "summary",
        created_at="2026-07-12T16:01:00Z",
    )
    generated_root = tmp_path / "data" / "generated"
    generated_root.mkdir(parents=True)
    output_root = generated_root / "research"
    target = output_root / revision.dossier_key / "handoffs" / f"{requested.handoff_id}.json"
    original_publish = research_artifacts.publish_file_atomic
    collision = ArtifactCollisionError("synthetic concurrent publication")

    def race_publish(actual_target: Path, payload: bytes) -> str:
        del payload
        assert actual_target == target
        original_publish(actual_target, _json_bytes(asdict(raced), sort_keys=True))
        raise collision

    monkeypatch.setattr(_writing_handoff(), "publish_file_atomic", race_publish)

    if semantic_match:
        publication = _api("publish_writing_handoff")(
            output_root,
            requested,
            generated_root=generated_root,
            acknowledge_unsafe=False,
        )
        assert publication.status == "reused"
        assert publication.package == raced
        assert publication.path == target
    else:
        with pytest.raises(ArtifactCollisionError) as raised:
            _api("publish_writing_handoff")(
                output_root,
                requested,
                generated_root=generated_root,
                acknowledge_unsafe=False,
            )
        assert raised.value is collision


def test_publish_handoff_custom_root_requires_location_ack_and_keeps_warning_out_of_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
) -> None:
    revision = _revision(dossier_manifest_builder, evidence_candidate_builder, citation_builder)
    handoff = _build_handoff(monkeypatch, revision)
    generated_root = tmp_path / "repo" / "data" / "generated"
    generated_root.mkdir(parents=True)
    custom_root = tmp_path / "shared"
    publish = _api("publish_writing_handoff")

    with pytest.raises(OutputRootAcknowledgementRequired):
        publish(
            custom_root,
            handoff,
            generated_root=generated_root,
            acknowledge_unsafe=False,
        )
    publication = publish(
        custom_root,
        handoff,
        generated_root=generated_root,
        acknowledge_unsafe=True,
    )

    assert publication.location_warning == "output_outside_generated_zone"
    assert "output_outside_generated_zone" not in publication.package.warnings


@pytest.mark.skipif(os.name != "posix", reason="symlink policy targets POSIX")
def test_publish_handoff_rejects_symlink_even_with_location_ack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
) -> None:
    revision = _revision(dossier_manifest_builder, evidence_candidate_builder, citation_builder)
    handoff = _build_handoff(monkeypatch, revision)
    generated_root = tmp_path / "generated"
    generated_root.mkdir()
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(UnsafeArtifactPathError):
        _api("publish_writing_handoff")(
            linked,
            handoff,
            generated_root=generated_root,
            acknowledge_unsafe=True,
        )
    assert list(real.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="no-follow input traversal targets POSIX")
@pytest.mark.parametrize("artifact_kind", ["handoff", "writing_output"])
def test_writing_loaders_reject_an_intermediate_symlink_component(
    tmp_path: Path,
    handoff_package_builder: Builder,
    writing_output_package_builder: Builder,
    artifact_kind: str,
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    if artifact_kind == "handoff":
        payload = handoff_package_builder()
        loader = _api("load_writing_handoff")
        error_type = _api("WritingHandoffError")
    else:
        payload = writing_output_package_builder()
        loader = _api("load_writing_output_package")
        error_type = _api("WritingOutputContractError")
    target = real / "artifact.json"
    target.write_bytes(_json_bytes(payload))

    with pytest.raises(error_type):
        loader(linked / target.name)


@pytest.mark.skipif(os.name != "posix", reason="atomic file publication targets POSIX")
def test_publish_handoff_cleans_same_parent_temporary_file_after_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
) -> None:
    revision = _revision(dossier_manifest_builder, evidence_candidate_builder, citation_builder)
    handoff = _build_handoff(monkeypatch, revision)
    generated_root = tmp_path / "data" / "generated"
    generated_root.mkdir(parents=True)
    output_root = generated_root / "research"

    def fail_link(source: object, target: object) -> None:
        del source, target
        raise OSError("synthetic atomic publication failure")

    monkeypatch.setattr(research_artifacts.os, "link", fail_link)
    with pytest.raises(OSError, match="synthetic atomic publication failure"):
        _api("publish_writing_handoff")(
            output_root,
            handoff,
            generated_root=generated_root,
            acknowledge_unsafe=False,
        )

    handoff_dir = output_root / revision.dossier_key / "handoffs"
    assert not (handoff_dir / f"{handoff.handoff_id}.json").exists()
    assert list(handoff_dir.iterdir()) == []


def _parsed_round_trip(
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
    handoff_package_builder: Builder,
    writing_output_package_builder: Builder,
    *,
    kind: str = "draft",
    content: str | None = None,
    sections: list[JsonObject] | None = None,
    output_overrides: Mapping[str, Any] | None = None,
) -> tuple[DossierRevision, Any, Any]:
    revision = _revision(
        dossier_manifest_builder,
        evidence_candidate_builder,
        citation_builder,
        states=("pinned", "selected"),
    )
    handoff_payload = handoff_package_builder(
        dossier_manifest=asdict(revision),
        requested_output={
            "kind": kind,
            "language": "ru",
            "style": "analytical and citation-aware",
            "max_words": 800,
        },
    )
    handoff = _api("parse_handoff_package")(_json_bytes(handoff_payload))
    effective_sections = deepcopy(sections)
    if effective_sections is not None:
        for section in effective_sections:
            if section.get("citation_ids") == ["__ALLOWLIST__"]:
                section["citation_ids"] = [handoff_payload["citation_allowlist"][0]]
    output_payload = writing_output_package_builder(
        handoff=handoff_payload,
        content_markdown=content,
        sections=effective_sections,
        **dict(output_overrides or {}),
    )
    output = _api("parse_writing_output_package")(_json_bytes(output_payload))
    return revision, handoff, output


def _materialized_imported_round_trip(
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
    handoff_package_builder: Builder,
    writing_output_package_builder: Builder,
) -> tuple[DossierRevision, Any, Any]:
    revision, handoff, output = _parsed_round_trip(
        dossier_manifest_builder,
        evidence_candidate_builder,
        citation_builder,
        handoff_package_builder,
        writing_output_package_builder,
    )
    _patch_dossier_validation(monkeypatch, revision)
    validation = _api("validate_writing_output_package")(object(), revision, handoff, output, validated_at=VALIDATED_AT)
    assert validation.status == "valid"
    package = research_artifacts.materialize_imported_writing_package(
        output,
        handoff,
        validation,
        imported_at="2026-07-12T16:05:00Z",
    )
    return revision, handoff, package


@pytest.mark.parametrize("kind", ["draft", "summary"])
def test_draft_and_summary_validate_and_prepare_import_symmetrically(
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
    handoff_package_builder: Builder,
    writing_output_package_builder: Builder,
    kind: str,
) -> None:
    revision, handoff, output = _parsed_round_trip(
        dossier_manifest_builder,
        evidence_candidate_builder,
        citation_builder,
        handoff_package_builder,
        writing_output_package_builder,
        kind=kind,
    )
    _patch_dossier_validation(monkeypatch, revision)

    validation = _api("validate_writing_output_package")(
        object(),
        revision,
        handoff,
        output,
        validated_at=VALIDATED_AT,
    )
    result = _api("prepare_writing_import")(
        object(),
        revision,
        handoff,
        output,
        validated_at=VALIDATED_AT,
    )

    assert isinstance(output, _api("WritingOutputPackage"))
    assert output.output_kind == handoff.requested_output.kind == kind
    output_payload = asdict(output)
    package_projection = {key: value for key, value in output_payload.items() if key != "package_digest"}
    assert output.content_sha256 == hashlib.sha256(output.content_markdown.encode("utf-8")).hexdigest()
    assert output.package_digest == canonical_sha256(package_projection)
    assert validation.status == "valid"
    assert validation.target_type == "writing_output"
    assert validation.target_id == output.package_digest
    assert validation.package_integrity is True
    assert validation.dossier_current is True
    assert validation.citations_resolved is True
    assert validation.coverage_complete is True
    assert validation.human_reviewed is False
    assert isinstance(result, _api("WritingImportResult"))
    assert result.handoff is handoff
    assert result.output is output
    assert result.validation == validation


def _semantic_output_override(
    case: str,
    handoff_package_builder: Builder,
    writing_section_builder: Builder,
) -> tuple[Mapping[str, Any], list[JsonObject] | None]:
    if case == "kind":
        return {"output_kind": "summary"}, None
    if case == "handoff_id":
        return {"handoff_id": "handoff-ffffffffffffffff"}, None
    if case == "handoff_digest":
        return {"handoff_digest": "0" * 64}, None
    if case == "dossier_key":
        return {"dossier_key": "research-other-0123456789ab"}, None
    if case == "revision_id":
        return {"revision_id": "rev-20260713T000000Z-89abcdef"}, None
    if case == "visibility":
        return {"visibility": "published_and_drafts", "includes_drafts": True}, None
    if case == "content_digest":
        return {"content_sha256": "0" * 64}, None
    if case == "package_digest":
        return {"package_digest": "0" * 64}, None
    if case == "unknown_citation":
        return {}, [writing_section_builder(citation_ids=["cit-deadbeefdeadbeef"])]
    raise AssertionError(case)  # pragma: no cover


@pytest.mark.parametrize(
    "case",
    [
        "kind",
        "handoff_id",
        "handoff_digest",
        "dossier_key",
        "revision_id",
        "visibility",
        "content_digest",
        "package_digest",
        "unknown_citation",
    ],
)
def test_output_identity_digest_visibility_and_allowlist_mismatches_reject_whole_import(
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
    handoff_package_builder: Builder,
    writing_output_package_builder: Builder,
    writing_section_builder: Builder,
    case: str,
) -> None:
    overrides, sections = _semantic_output_override(case, handoff_package_builder, writing_section_builder)
    revision, handoff, output = _parsed_round_trip(
        dossier_manifest_builder,
        evidence_candidate_builder,
        citation_builder,
        handoff_package_builder,
        writing_output_package_builder,
        sections=sections,
        output_overrides=overrides,
    )
    _patch_dossier_validation(monkeypatch, revision)

    validation = _api("validate_writing_output_package")(
        object(),
        revision,
        handoff,
        output,
        validated_at=VALIDATED_AT,
    )

    assert validation.status == "invalid"
    assert validation.errors
    with pytest.raises(_api("WritingImportError")) as raised:
        _api("prepare_writing_import")(
            object(),
            revision,
            handoff,
            output,
            validated_at=VALIDATED_AT,
        )
    assert raised.value.code == "writing_output_invalid"
    assert raised.value.validation.status == "invalid"


def test_self_consistent_forged_handoff_cannot_expand_revision_evidence(
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
    handoff_package_builder: Builder,
    writing_output_package_builder: Builder,
    writing_section_builder: Builder,
) -> None:
    revision = _revision(
        dossier_manifest_builder,
        evidence_candidate_builder,
        citation_builder,
        states=("pinned", "selected"),
    )
    selected = [
        asdict(candidate.citation)
        for candidate in revision.candidate_evidence
        if candidate.citation.citation_id in revision.selected_citation_ids
    ]
    forged = _citation(citation_builder, "forged-added-evidence")
    handoff_payload = handoff_package_builder(
        dossier_manifest=asdict(revision),
        evidence=[*selected, forged],
    )
    handoff = _api("parse_handoff_package")(_json_bytes(handoff_payload))
    content = "Self-consistent but unauthorized evidence."
    output_payload = writing_output_package_builder(
        handoff=handoff_payload,
        content_markdown=content,
        sections=[
            writing_section_builder(
                content_markdown=content,
                char_start=0,
                char_end=len(content),
                citation_ids=[forged["citation_id"]],
            )
        ],
    )
    output = _api("parse_writing_output_package")(_json_bytes(output_payload))
    _patch_dossier_validation(monkeypatch, revision)

    validation = _api("validate_writing_output_package")(object(), revision, handoff, output, validated_at=VALIDATED_AT)

    assert validation.status == "invalid"
    assert validation.package_integrity is True
    assert validation.dossier_current is False
    forged_state = next(row for row in validation.citations if row["citation_id"] == forged["citation_id"])
    assert forged_state["status"] == "missing"
    with pytest.raises(_api("WritingImportError")) as raised:
        _api("prepare_writing_import")(object(), revision, handoff, output, validated_at=VALIDATED_AT)
    assert raised.value.code == "writing_output_invalid"
    assert raised.value.validation.status == "invalid"


def test_unicode_section_ranges_are_ordered_unique_contiguous_and_exhaustive(
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
    handoff_package_builder: Builder,
    writing_output_package_builder: Builder,
    writing_section_builder: Builder,
) -> None:
    content = "А🧭Б\nГ"
    sections = [
        writing_section_builder(
            content_markdown=content,
            section_id="section-unicode-a",
            heading="А🧭",
            char_start=0,
            char_end=2,
            citation_ids=["__ALLOWLIST__"],
        ),
        writing_section_builder(
            content_markdown=content,
            section_id="section-unicode-b",
            heading="Б и Г",
            char_start=2,
            char_end=len(content),
            citation_ids=["__ALLOWLIST__"],
        ),
    ]
    revision, handoff, output = _parsed_round_trip(
        dossier_manifest_builder,
        evidence_candidate_builder,
        citation_builder,
        handoff_package_builder,
        writing_output_package_builder,
        content=content,
        sections=sections,
    )
    _patch_dossier_validation(monkeypatch, revision)

    validation = _api("validate_writing_output_package")(object(), revision, handoff, output, validated_at=VALIDATED_AT)

    assert len(content) == 5
    assert len(content.encode("utf-8")) > len(content)
    assert validation.status == "valid"
    assert validation.coverage_complete is True


@pytest.mark.parametrize("case", ["gap", "overlap", "out_of_order", "not_exhaustive", "duplicate_id"])
def test_invalid_section_coverage_is_reported_without_partial_import(
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
    handoff_package_builder: Builder,
    writing_output_package_builder: Builder,
    writing_section_builder: Builder,
    case: str,
) -> None:
    content = "abcdef"
    left_end, right_start, right_end = 3, 3, len(content)
    if case == "gap":
        left_end, right_start = 2, 3
    elif case == "overlap":
        left_end, right_start = 4, 3
    elif case == "not_exhaustive":
        right_end = len(content) - 1
    sections = [
        writing_section_builder(
            content_markdown=content,
            section_id="section-a",
            char_start=0,
            char_end=left_end,
            citation_ids=["__ALLOWLIST__"],
        ),
        writing_section_builder(
            content_markdown=content,
            section_id="section-a" if case == "duplicate_id" else "section-b",
            char_start=right_start,
            char_end=right_end,
            citation_ids=["__ALLOWLIST__"],
        ),
    ]
    if case == "out_of_order":
        sections.reverse()
    revision, handoff, output = _parsed_round_trip(
        dossier_manifest_builder,
        evidence_candidate_builder,
        citation_builder,
        handoff_package_builder,
        writing_output_package_builder,
        content=content,
        sections=sections,
    )
    _patch_dossier_validation(monkeypatch, revision)

    validation = _api("validate_writing_output_package")(object(), revision, handoff, output, validated_at=VALIDATED_AT)

    assert validation.status == "invalid"
    assert validation.coverage_complete is False
    with pytest.raises(_api("WritingImportError")):
        _api("prepare_writing_import")(object(), revision, handoff, output, validated_at=VALIDATED_AT)


@pytest.mark.parametrize(
    ("citation_ids", "unsupported", "reason", "accepted", "warns"),
    [
        ("allowlisted", False, None, True, False),
        ("empty", True, "Корпус не содержит подтверждения.", True, True),
        ("empty", False, None, False, False),
        ("empty", True, None, False, False),
        ("allowlisted", False, "reason forbidden for supported section", False, False),
        ("allowlisted", True, "ambiguous supported and unsupported state", False, False),
    ],
)
def test_unsupported_section_truth_table_and_warnings(
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
    handoff_package_builder: Builder,
    writing_output_package_builder: Builder,
    writing_section_builder: Builder,
    citation_ids: str,
    unsupported: bool,
    reason: str | None,
    accepted: bool,
    warns: bool,
) -> None:
    content = "Synthetic section."
    sections = [
        writing_section_builder(
            content_markdown=content,
            char_start=0,
            char_end=len(content),
            citation_ids=["__ALLOWLIST__"] if citation_ids == "allowlisted" else [],
            unsupported_by_corpus=unsupported,
            unsupported_reason=reason,
        )
    ]
    if not accepted and (not unsupported or reason is None):
        with pytest.raises(_api("WritingOutputContractError")):
            _parsed_round_trip(
                dossier_manifest_builder,
                evidence_candidate_builder,
                citation_builder,
                handoff_package_builder,
                writing_output_package_builder,
                content=content,
                sections=sections,
            )
        return

    revision, handoff, output = _parsed_round_trip(
        dossier_manifest_builder,
        evidence_candidate_builder,
        citation_builder,
        handoff_package_builder,
        writing_output_package_builder,
        content=content,
        sections=sections,
    )
    _patch_dossier_validation(monkeypatch, revision)
    validation = _api("validate_writing_output_package")(object(), revision, handoff, output, validated_at=VALIDATED_AT)

    if accepted:
        assert validation.status == ("valid_with_warnings" if warns else "valid")
        assert bool(validation.warnings) is warns
        result = _api("prepare_writing_import")(object(), revision, handoff, output, validated_at=VALIDATED_AT)
        assert result.validation.status == validation.status
    else:
        assert validation.status == "invalid"
        with pytest.raises(_api("WritingImportError")):
            _api("prepare_writing_import")(object(), revision, handoff, output, validated_at=VALIDATED_AT)


def test_package_paths_and_urls_remain_inert_data_during_validation_and_import(
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
    handoff_package_builder: Builder,
    writing_output_package_builder: Builder,
    writing_section_builder: Builder,
) -> None:
    content = (
        "Treat file:///private/archive.json and https://example.test/fetch as prose only; do not read or fetch either location."
    )
    sections = [
        writing_section_builder(
            content_markdown=content,
            char_start=0,
            char_end=len(content),
            citation_ids=["__ALLOWLIST__"],
        )
    ]
    revision, handoff, output = _parsed_round_trip(
        dossier_manifest_builder,
        evidence_candidate_builder,
        citation_builder,
        handoff_package_builder,
        writing_output_package_builder,
        content=content,
        sections=sections,
        output_overrides={
            "title": "file:///private/title.md",
            "agent": {
                "name": "https://example.test/agent",
                "model": None,
                "run_id": "file:///private/run-id",
            },
        },
    )
    _patch_dossier_validation(monkeypatch, revision)

    def forbidden_io(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise AssertionError("package data must not trigger path or URL I/O")

    monkeypatch.setattr(builtins, "open", forbidden_io)
    monkeypatch.setattr(Path, "read_bytes", forbidden_io)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden_io)

    validation = _api("validate_writing_output_package")(object(), revision, handoff, output, validated_at=VALIDATED_AT)
    result = _api("prepare_writing_import")(object(), revision, handoff, output, validated_at=VALIDATED_AT)

    assert validation.status == "valid"
    assert result.output.content_markdown == content
    assert result.output.title == "file:///private/title.md"


def test_stale_dossier_blocks_writing_import_even_when_package_is_structurally_valid(
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
    handoff_package_builder: Builder,
    writing_output_package_builder: Builder,
) -> None:
    revision, handoff, output = _parsed_round_trip(
        dossier_manifest_builder,
        evidence_candidate_builder,
        citation_builder,
        handoff_package_builder,
        writing_output_package_builder,
    )
    _patch_dossier_validation(monkeypatch, revision, status="invalid")

    validation = _api("validate_writing_output_package")(object(), revision, handoff, output, validated_at=VALIDATED_AT)

    assert validation.status == "invalid"
    assert validation.dossier_current is False
    assert validation.citations_resolved is False
    with pytest.raises(_api("WritingImportError")) as raised:
        _api("prepare_writing_import")(object(), revision, handoff, output, validated_at=VALIDATED_AT)
    assert raised.value.code == "writing_output_invalid"
    assert raised.value.validation == validation


def test_imported_validation_reports_malformed_contract_as_schema_invalid(
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
    handoff_package_builder: Builder,
    writing_output_package_builder: Builder,
) -> None:
    revision, handoff, package = _materialized_imported_round_trip(
        monkeypatch,
        dossier_manifest_builder,
        evidence_candidate_builder,
        citation_builder,
        handoff_package_builder,
        writing_output_package_builder,
    )
    malformed = SimpleNamespace(
        manifest={**deepcopy(package.manifest), "provider_api_key": PRIVATE_MARKER},
        validation=deepcopy(package.validation),
        markdown=package.markdown,
        files=deepcopy(package.files),
    )

    validation = _api("validate_imported_writing_package")(object(), revision, handoff, malformed, validated_at=VALIDATED_AT)

    assert validation.status == "invalid"
    assert validation.schema_valid is False
    assert validation.errors


def test_imported_validation_keeps_intrinsic_integrity_when_only_dossier_is_stale(
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
    handoff_package_builder: Builder,
    writing_output_package_builder: Builder,
) -> None:
    revision, handoff, package = _materialized_imported_round_trip(
        monkeypatch,
        dossier_manifest_builder,
        evidence_candidate_builder,
        citation_builder,
        handoff_package_builder,
        writing_output_package_builder,
    )
    _patch_dossier_validation(monkeypatch, revision, status="invalid")

    validation = _api("validate_imported_writing_package")(object(), revision, handoff, package, validated_at=VALIDATED_AT)

    assert validation.status == "invalid"
    assert validation.schema_valid is True
    assert validation.package_integrity is True
    assert validation.dossier_current is False
    assert validation.citations_resolved is False


def test_imported_manifest_output_kind_is_bound_to_handoff_request(
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
    handoff_package_builder: Builder,
    writing_output_package_builder: Builder,
) -> None:
    revision, handoff, package = _materialized_imported_round_trip(
        monkeypatch,
        dossier_manifest_builder,
        evidence_candidate_builder,
        citation_builder,
        handoff_package_builder,
        writing_output_package_builder,
    )
    manifest = deepcopy(package.manifest)
    manifest["output_kind"] = "summary"
    forged = SimpleNamespace(
        manifest=manifest,
        validation=deepcopy(package.validation),
        markdown=package.markdown,
        files={
            **deepcopy(package.files),
            "manifest.json": (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
        },
    )

    validation = _api("validate_imported_writing_package")(object(), revision, handoff, forged, validated_at=VALIDATED_AT)

    assert handoff.requested_output.kind == "draft"
    assert validation.status == "invalid"
    assert validation.schema_valid is True
    assert validation.package_integrity is True
    assert validation.dossier_current is False
    assert validation.errors


def test_dossier_validation_operational_failure_is_safe_and_non_publishable(
    monkeypatch: pytest.MonkeyPatch,
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
    handoff_package_builder: Builder,
) -> None:
    revision = _revision(dossier_manifest_builder, evidence_candidate_builder, citation_builder)
    handoff = _api("parse_handoff_package")(_json_bytes(handoff_package_builder(dossier_manifest=asdict(revision))))

    def unavailable(*args: Any, **kwargs: Any) -> ValidationResult:
        del args, kwargs
        raise DossierValidationError("private operational diagnostic")

    monkeypatch.setattr(research_workflow, "validate_dossier_revision", unavailable)
    monkeypatch.setattr(_writing_handoff(), "validate_dossier_revision", unavailable, raising=False)

    validation = _api("validate_writing_handoff")(object(), revision, handoff, validated_at=VALIDATED_AT)
    assert validation.status == "invalid"
    assert validation.dossier_current is False
    assert "private operational diagnostic" not in repr(validation)

    with pytest.raises(_api("WritingHandoffError")) as raised:
        _api("build_writing_handoff")(
            object(),
            revision,
            _requested_output(),
            egress_acknowledged=True,
            allow_draft_evidence=False,
            validated_at=VALIDATED_AT,
            created_at=CREATED_AT,
        )
    assert raised.value.code == "dossier_validation_unavailable"
    assert "private operational diagnostic" not in str(raised.value)


def test_error_types_expose_only_stable_code_and_optional_validation(
    dossier_manifest_builder: Builder,
    evidence_candidate_builder: Builder,
    citation_builder: Builder,
) -> None:
    revision = _revision(dossier_manifest_builder, evidence_candidate_builder, citation_builder)
    invalid = _validation(revision, status="invalid")

    handoff_error = _api("WritingHandoffError")("dossier_not_current", invalid)
    import_error = _api("WritingImportError")("writing_output_invalid", invalid)

    assert (handoff_error.code, handoff_error.validation) == ("dossier_not_current", invalid)
    assert (import_error.code, import_error.validation) == ("writing_output_invalid", invalid)
    assert PRIVATE_MARKER not in str(handoff_error)
    assert PRIVATE_MARKER not in str(import_error)


def test_writing_handoff_public_api_is_complete() -> None:
    module = _writing_handoff()
    expected = {
        "RequestedWritingOutput",
        "HandoffPackage",
        "WritingOutputPackage",
        "HandoffPublication",
        "WritingImportResult",
        "WritingHandoffError",
        "WritingOutputContractError",
        "WritingImportError",
        "parse_handoff_package",
        "load_writing_handoff",
        "build_writing_handoff",
        "publish_writing_handoff",
        "validate_writing_handoff",
        "parse_writing_output_package",
        "load_writing_output_package",
        "validate_writing_output_package",
        "prepare_writing_import",
    }

    assert expected <= set(vars(module))
    assert "jsonschema" not in sys.modules or "jsonschema" not in vars(module)
