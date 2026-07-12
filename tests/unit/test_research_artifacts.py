from __future__ import annotations

import hashlib
import json
import os
import stat
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from knowledge_base.research_artifacts import (
    ArtifactCollisionError,
    ArtifactContractError,
    OutputRootAcknowledgementRequired,
    ShortIdCollisionError,
    ShortIdRegistry,
    UnsafeArtifactPathError,
    assert_no_symlink_components,
    canonical_json_bytes,
    canonical_sha256,
    materialize_dossier_package,
    parse_strict_object,
    publish_directory_atomic,
    publish_dossier_package,
    publish_file_atomic,
    safe_http_url,
    validate_output_root,
)

CONTRACT_DIR = Path(__file__).resolve().parents[2] / "specs" / "007-writer-research-workflow" / "contracts"


def _schemas() -> dict[str, dict]:
    return {path.name: json.loads(path.read_text(encoding="utf-8")) for path in sorted(CONTRACT_DIR.glob("*.schema.json"))}


def _registry(schemas: dict[str, dict]) -> Registry:
    return Registry().with_resources((schema["$id"], Resource.from_contents(schema)) for schema in schemas.values())


def test_all_six_contract_schemas_are_valid_draft_2020_12() -> None:
    schemas = _schemas()

    assert set(schemas) == {
        "citation.schema.json",
        "dossier-manifest.schema.json",
        "handoff-package.schema.json",
        "imported-writing-manifest.schema.json",
        "validation-result.schema.json",
        "writing-output-package.schema.json",
    }
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)


def test_synthetic_citation_dossier_and_validation_match_contracts(
    citation_builder,
    dossier_manifest_builder,
    dossier_package_builder,
) -> None:
    schemas = _schemas()
    registry = _registry(schemas)
    checker = FormatChecker()
    citation = citation_builder()
    manifest = dossier_manifest_builder(candidate_evidence=None)
    package = dossier_package_builder(manifest=manifest)

    Draft202012Validator(schemas["citation.schema.json"], registry=registry, format_checker=checker).validate(citation)
    Draft202012Validator(schemas["dossier-manifest.schema.json"], registry=registry, format_checker=checker).validate(
        package.manifest
    )
    Draft202012Validator(schemas["validation-result.schema.json"], registry=registry, format_checker=checker).validate(
        package.validation
    )


def test_canonical_json_is_compact_sorted_utf8_and_digestible() -> None:
    left = {"z": "Привет", "a": [2, 1]}
    right = {"a": [2, 1], "z": "Привет"}
    expected = '{"a":[2,1],"z":"Привет"}'.encode()

    assert canonical_json_bytes(left) == expected
    assert canonical_json_bytes(right) == expected
    assert canonical_sha256(left) == hashlib.sha256(expected).hexdigest()
    with pytest.raises(ValueError):
        canonical_json_bytes({"not_json": float("nan")})


def test_short_id_registry_is_idempotent_and_rejects_prefix_collision() -> None:
    registry = ShortIdRegistry(prefix="cit", length=16)
    first = "a" * 64
    collision = "a" * 16 + "b" * 48

    assert registry.register(first) == "cit-" + "a" * 16
    assert registry.register(first) == "cit-" + "a" * 16
    with pytest.raises(ShortIdCollisionError):
        registry.register(collision)


def test_strict_object_parser_accepts_only_expected_version_and_fields() -> None:
    payload = b'{"schema_version":"1.0","artifact_type":"synthetic","value":1}'

    assert (
        parse_strict_object(
            payload,
            artifact_type="synthetic",
            required_fields={"value"},
            optional_fields={"note"},
            max_bytes=256,
        )["value"]
        == 1
    )

    invalid_payloads = [
        b"[]",
        b'{"schema_version":"2.0","artifact_type":"synthetic","value":1}',
        b'{"schema_version":"1.0","artifact_type":"other","value":1}',
        b'{"schema_version":"1.0","artifact_type":"synthetic"}',
        b'{"schema_version":"1.0","artifact_type":"synthetic","value":1,"extra":2}',
        b'{"schema_version":"1.0","artifact_type":"synthetic","value":1,"value":2}',
    ]
    for invalid in invalid_payloads:
        with pytest.raises(ArtifactContractError):
            parse_strict_object(
                invalid,
                artifact_type="synthetic",
                required_fields={"value"},
                optional_fields={"note"},
                max_bytes=256,
            )

    with pytest.raises(ArtifactContractError):
        parse_strict_object(
            payload + b" " * 256,
            artifact_type="synthetic",
            required_fields={"value"},
            max_bytes=256,
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://example.test/Article", "https://example.test/Article"),
        ("http://example.test/item", "http://example.test/item"),
        ("HTTPS://example.test/item", "https://example.test/item"),
        ("file:///private/archive.json", None),
        ("javascript:alert(1)", None),
        ("/relative/path", None),
        ("https://user:secret@example.test/item", None),
        (None, None),
    ],
)
def test_safe_http_url_projection(value: object, expected: str | None) -> None:
    assert safe_http_url(value) == expected


def test_output_root_outside_generated_zone_requires_acknowledgement(tmp_path: Path) -> None:
    generated = tmp_path / "repo" / "data" / "generated"
    generated.mkdir(parents=True)
    inside = generated / "research"
    outside = tmp_path / "shared"

    assert validate_output_root(inside, generated_root=generated, acknowledge_unsafe=False) is None
    with pytest.raises(OutputRootAcknowledgementRequired):
        validate_output_root(outside, generated_root=generated, acknowledge_unsafe=False)
    assert validate_output_root(outside, generated_root=generated, acknowledge_unsafe=True) == "output_outside_generated_zone"


@pytest.mark.skipif(os.name != "posix", reason="V5 targets POSIX filesystem semantics")
def test_symlink_in_any_existing_path_component_is_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(UnsafeArtifactPathError):
        assert_no_symlink_components(linked / "nested" / "artifact")


@pytest.mark.skipif(os.name != "posix", reason="V5 targets POSIX filesystem semantics")
def test_atomic_directory_publication_sets_modes_and_handles_collisions(tmp_path: Path) -> None:
    target = tmp_path / "revisions" / "rev-1"
    files = {"manifest.json": b"{}\n", "dossier.md": b"# Dossier\n", "validation.json": b"{}\n"}

    assert publish_directory_atomic(target, files) == "created"
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    assert all(stat.S_IMODE((target / name).stat().st_mode) == 0o600 for name in files)
    assert publish_directory_atomic(target, files) == "reused"

    with pytest.raises(ArtifactCollisionError):
        publish_directory_atomic(target, {**files, "dossier.md": b"changed\n"})
    assert (target / "dossier.md").read_bytes() == files["dossier.md"]
    assert {path.name for path in target.parent.iterdir()} == {target.name}


@pytest.mark.skipif(os.name != "posix", reason="V5 targets POSIX filesystem semantics")
def test_atomic_standalone_file_publication_sets_modes_and_cleans_up(tmp_path: Path) -> None:
    target = tmp_path / "handoffs" / "handoff-1.json"
    payload = b'{"artifact_type":"writing_handoff"}\n'

    assert publish_file_atomic(target, payload) == "created"
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert publish_file_atomic(target, payload) == "reused"

    with pytest.raises(ArtifactCollisionError):
        publish_file_atomic(target, b"different")
    assert target.read_bytes() == payload
    assert {path.name for path in target.parent.iterdir()} == {target.name}


def test_directory_publication_rejects_relative_path_escape_without_partial_target(tmp_path: Path) -> None:
    target = tmp_path / "revision"

    with pytest.raises(UnsafeArtifactPathError):
        publish_directory_atomic(target, {"../escaped": b"no"})
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_dossier_key_and_content_digest_are_deterministic_across_run_identity(
    research_request_builder,
    evidence_candidate_builder,
    dossier_manifest_builder,
) -> None:
    request = research_request_builder()
    candidate = evidence_candidate_builder()
    context = dossier_manifest_builder()["corpus_context"]
    changed_context = deepcopy(context)
    changed_context.update(
        built_at="2026-07-13T12:00:00Z",
        latest_import_run_key="import-later",
        latest_index_runs={"embeddings": {"run_key": "index-later"}},
    )

    first = _materialize(request, context, [candidate], clock="2026-07-12T12:00:00Z", entropy="01234567")
    repeated = _materialize(
        request,
        changed_context,
        [candidate],
        clock="2026-07-13T12:00:00Z",
        entropy="89abcdef",
    )
    other_scope = _materialize(
        {**request, "visibility": "published_and_drafts"},
        context,
        [candidate],
        clock="2026-07-12T12:00:00Z",
        entropy="fedcba98",
    )

    assert first.manifest["dossier_key"] == repeated.manifest["dossier_key"]
    assert first.manifest["content_digest"] == repeated.manifest["content_digest"]
    assert first.manifest["revision_id"] != repeated.manifest["revision_id"]
    assert other_scope.manifest["dossier_key"] != first.manifest["dossier_key"]
    assert other_scope.manifest["content_digest"] != first.manifest["content_digest"]


def test_markdown_has_exact_selected_order_and_escapes_untrusted_unicode_content(
    research_request_builder,
    citation_builder,
    evidence_candidate_builder,
    dossier_manifest_builder,
) -> None:
    unsafe_excerpt = "Привет 🧭\x00\n# forged heading\n<script>alert(1)</script>\x7f"
    unsafe = citation_builder(
        canonical_id="unsafe-unicode",
        document_key="doc-unsafe-unicode",
        chunk_key="chunk-unsafe-unicode-0",
        excerpt=unsafe_excerpt,
        char_end=len(unsafe_excerpt),
        title="Unicode </h2> 🧪",
    )
    second = citation_builder(
        canonical_id="second",
        document_key="doc-second",
        chunk_key="chunk-second-0",
        excerpt="Second selected excerpt.",
        char_end=len("Second selected excerpt."),
    )
    unselected = citation_builder(
        canonical_id="candidate-only",
        document_key="doc-candidate-only",
        chunk_key="chunk-candidate-only-0",
        excerpt="This candidate must not be rendered.",
        char_end=len("This candidate must not be rendered."),
    )
    candidates = [
        evidence_candidate_builder(citation=second, selection_state="pinned"),
        evidence_candidate_builder(citation=unsafe, selection_state="selected"),
        evidence_candidate_builder(citation=unselected, selection_state="candidate"),
    ]

    package = _materialize(
        research_request_builder(query="Unicode dossier 🧭"),
        dossier_manifest_builder()["corpus_context"],
        candidates,
        clock="2026-07-12T12:00:00Z",
        entropy="01234567",
    )

    markdown_ids = [line[3:] for line in package.markdown.splitlines() if line.startswith("## cit-")]
    assert (
        markdown_ids
        == package.manifest["selected_citation_ids"]
        == [
            second["citation_id"],
            unsafe["citation_id"],
        ]
    )
    assert unselected["citation_id"] not in package.markdown
    assert "Привет 🧭" in package.markdown
    assert "\\u0000" in package.markdown and "\\u007f" in package.markdown
    assert "<script>" not in package.markdown and "</h2>" not in package.markdown
    assert "\n# forged heading" not in package.markdown
    assert all(character == "\n" or 0x20 <= ord(character) < 0x7F or ord(character) >= 0xA0 for character in package.markdown)


@pytest.mark.parametrize(
    ("warnings", "manifest_status", "validation_status"),
    [([], "ready", "valid"), (["derived indexes are stale"], "degraded", "valid_with_warnings")],
)
def test_materialization_builds_ready_or_degraded_manifest_and_initial_validation(
    research_request_builder,
    evidence_candidate_builder,
    dossier_manifest_builder,
    warnings: list[str],
    manifest_status: str,
    validation_status: str,
) -> None:
    context = deepcopy(dossier_manifest_builder()["corpus_context"])
    context["warnings"] = warnings

    package = _materialize(
        research_request_builder(),
        context,
        [evidence_candidate_builder()],
        clock="2026-07-12T12:00:00Z",
        entropy="01234567",
    )

    manifest = package.manifest
    validation = package.validation
    assert manifest["status"] == manifest_status
    assert manifest["warnings"] == warnings
    assert validation["status"] == validation_status
    assert validation["target_id"] == manifest["revision_id"]
    assert validation["target_digest"] == manifest["content_digest"]
    assert validation["validated_at"] == "2026-07-12T12:00:00Z"
    assert validation["warnings"] == warnings and validation["errors"] == []
    assert [row["citation_id"] for row in validation["citations"]] == manifest["selected_citation_ids"]
    assert all(
        validation[field]
        for field in ("schema_valid", "package_integrity", "dossier_current", "citations_resolved", "coverage_complete")
    )
    assert validation["human_reviewed"] is False


@pytest.mark.parametrize(
    ("request_overrides", "citation_overrides", "context_overrides"),
    [
        ({"published_from": "not-a-date"}, {}, {}),
        ({"published_from": "2026-02-01", "published_to": "2026-01-01"}, {}, {}),
        ({}, {"chunk_ordinal": -1}, {}),
        ({}, {"published_at": "not-a-date"}, {}),
        ({}, {"captured_at": "not-a-date"}, {}),
        ({}, {"published_at": "2026-01-15 10:00:00Z"}, {}),
        ({}, {}, {"built_at": "2026-01-15 10:00:00Z"}),
    ],
)
def test_materialization_rejects_schema_invalid_request_and_citation_values(
    research_request_builder,
    citation_builder,
    evidence_candidate_builder,
    dossier_manifest_builder,
    request_overrides: dict[str, Any],
    citation_overrides: dict[str, Any],
    context_overrides: dict[str, Any],
) -> None:
    candidate = evidence_candidate_builder(citation=citation_builder(**citation_overrides))
    context = {**dossier_manifest_builder()["corpus_context"], **context_overrides}

    with pytest.raises(ArtifactContractError):
        _materialize(
            research_request_builder(**request_overrides),
            context,
            [candidate],
            clock="2026-07-12T12:00:00Z",
            entropy="01234567",
        )


def test_draft_visibility_warning_is_informational_and_explicit_degradation_stays_distinct(
    research_request_builder,
    evidence_candidate_builder,
    dossier_manifest_builder,
) -> None:
    context = dossier_manifest_builder()["corpus_context"]
    request = research_request_builder(visibility="published_and_drafts")
    draft = _materialize(
        request,
        context,
        [evidence_candidate_builder()],
        clock="2026-07-12T12:00:00Z",
        entropy="01234567",
    )

    assert draft.manifest["status"] == "ready"
    assert draft.manifest["warnings"] == ["draft_visibility_enabled"]
    assert draft.validation["status"] == "valid_with_warnings"
    assert draft.validation["warnings"] == ["draft_visibility_enabled"]
    assert "draft_visibility_enabled" in draft.markdown

    degraded = materialize_dossier_package(
        request=request,
        corpus_context=context,
        candidate_evidence=[evidence_candidate_builder()],
        derived_context={"topics": [], "leads": []},
        warnings=["optional related context is unavailable"],
        status="degraded",
        clock=lambda: "2026-07-12T12:00:00Z",
        entropy=lambda: "89abcdef",
    )
    assert degraded.manifest["status"] == "degraded"
    assert degraded.validation["status"] == "valid_with_warnings"


def test_no_selected_evidence_refuses_materialization_and_publishes_nothing(
    tmp_path: Path,
    research_request_builder,
    evidence_candidate_builder,
    dossier_manifest_builder,
) -> None:
    output_root = tmp_path / "research"

    with pytest.raises(ArtifactContractError, match="evidence"):
        _materialize(
            research_request_builder(),
            dossier_manifest_builder()["corpus_context"],
            [evidence_candidate_builder(selection_state="candidate")],
            clock="2026-07-12T12:00:00Z",
            entropy="01234567",
        )

    assert not output_root.exists()


@pytest.mark.skipif(os.name != "posix", reason="V5 targets POSIX filesystem semantics")
def test_root_package_is_atomic_immutable_and_contains_exactly_three_owner_only_files(
    tmp_path: Path,
    research_request_builder,
    evidence_candidate_builder,
    dossier_manifest_builder,
) -> None:
    output_root = tmp_path / "research"
    args = (research_request_builder(), dossier_manifest_builder()["corpus_context"], [evidence_candidate_builder()])
    first = _materialize(*args, clock="2026-07-12T12:00:00Z", entropy="01234567")

    assert publish_dossier_package(output_root, first) == "created"
    first_path = output_root / first.manifest["dossier_key"] / "revisions" / first.manifest["revision_id"]
    original = {path.name: path.read_bytes() for path in first_path.iterdir()}
    assert original == first.files
    assert set(original) == {"manifest.json", "dossier.md", "validation.json"}
    assert stat.S_IMODE(first_path.stat().st_mode) == 0o700
    assert all(stat.S_IMODE((first_path / name).stat().st_mode) == 0o600 for name in original)
    assert publish_dossier_package(output_root, first) == "reused"

    second = _materialize(*args, clock="2026-07-12T12:00:01Z", entropy="89abcdef")
    assert publish_dossier_package(output_root, second) == "created"
    assert {path.name for path in first_path.parent.iterdir()} == {
        first.manifest["revision_id"],
        second.manifest["revision_id"],
    }
    assert {path.name: path.read_bytes() for path in first_path.iterdir()} == original


def _materialize(
    request: dict[str, Any],
    corpus_context: dict[str, Any],
    candidate_evidence: list[dict[str, Any]],
    *,
    clock: str,
    entropy: str,
) -> Any:
    return materialize_dossier_package(
        request=request,
        corpus_context=corpus_context,
        candidate_evidence=candidate_evidence,
        derived_context={"topics": [], "leads": []},
        clock=lambda: clock,
        entropy=lambda: entropy,
    )
