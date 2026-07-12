from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

import knowledge_base.research_artifacts as research_artifacts_module
from knowledge_base.research_artifacts import (
    ArtifactCollisionError,
    ArtifactContractError,
    DossierPackage,
    OutputRootAcknowledgementRequired,
    ShortIdCollisionError,
    ShortIdRegistry,
    UnsafeArtifactPathError,
    assert_no_symlink_components,
    canonical_json_bytes,
    canonical_sha256,
    materialize_curated_dossier_package,
    materialize_dossier_package,
    parse_strict_object,
    publish_directory_atomic,
    publish_dossier_package,
    publish_file_atomic,
    safe_http_url,
    validate_output_root,
)

CONTRACT_DIR = Path(__file__).resolve().parents[2] / "specs" / "007-writer-research-workflow" / "contracts"
_USERINFO_URL = "https://" + ":".join(("synthetic-user", "synthetic-value@example.test/item"))
_OVERSIZED_DOSSIER_FILE_BYTES = 32 * 1024 * 1024 + 1


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
    invalid_json = {"not_json": float("nan")}
    with pytest.raises(ValueError):
        canonical_json_bytes(invalid_json)


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
        (_USERINFO_URL, None),
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
    request = research_request_builder(**request_overrides)

    with pytest.raises(ArtifactContractError):
        _materialize(
            request,
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
    request = research_request_builder()
    context = dossier_manifest_builder()["corpus_context"]
    candidates = [evidence_candidate_builder(selection_state="candidate")]

    with pytest.raises(ArtifactContractError, match="evidence"):
        _materialize(
            request,
            context,
            candidates,
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


def test_load_dossier_package_returns_exact_materialized_three_file_projection(
    tmp_path: Path,
    research_request_builder,
    evidence_candidate_builder,
    dossier_manifest_builder,
) -> None:
    package, revision_path = _publish_materialized_package(
        tmp_path / "research",
        research_request_builder(),
        dossier_manifest_builder()["corpus_context"],
        [evidence_candidate_builder()],
    )

    loaded = _load_dossier_package(revision_path)

    assert isinstance(loaded, DossierPackage)
    assert loaded.manifest == package.manifest
    assert loaded.validation == package.validation
    assert loaded.markdown == package.markdown
    assert loaded.files == package.files
    assert set(loaded.files) == {"manifest.json", "dossier.md", "validation.json"}


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_manifest",
        "missing_dossier",
        "missing_validation",
        "unknown_file",
    ],
)
def test_load_dossier_package_requires_exactly_three_known_files(
    tmp_path: Path,
    research_request_builder,
    evidence_candidate_builder,
    dossier_manifest_builder,
    mutation: str,
) -> None:
    _, revision_path = _publish_materialized_package(
        tmp_path / "research",
        research_request_builder(),
        dossier_manifest_builder()["corpus_context"],
        [evidence_candidate_builder()],
    )
    if mutation == "unknown_file":
        (revision_path / "notes.txt").write_text("must not be ignored", encoding="utf-8")
    else:
        filename = mutation.removeprefix("missing_")
        {
            "manifest": revision_path / "manifest.json",
            "dossier": revision_path / "dossier.md",
            "validation": revision_path / "validation.json",
        }[filename].unlink()

    with pytest.raises(ArtifactContractError):
        _load_dossier_package(revision_path)


@pytest.mark.parametrize("filename", ["manifest.json", "dossier.md", "validation.json"])
def test_load_dossier_package_rejects_oversized_members_before_reading_unbounded_data(
    tmp_path: Path,
    research_request_builder,
    evidence_candidate_builder,
    dossier_manifest_builder,
    filename: str,
) -> None:
    _, revision_path = _publish_materialized_package(
        tmp_path / "research",
        research_request_builder(),
        dossier_manifest_builder()["corpus_context"],
        [evidence_candidate_builder()],
    )
    with (revision_path / filename).open("wb") as handle:
        handle.truncate(_OVERSIZED_DOSSIER_FILE_BYTES)

    with pytest.raises(ArtifactContractError, match=r"(?i:byte|size|limit|large|exceed)"):
        _load_dossier_package(revision_path)


@pytest.mark.parametrize("filename", ["manifest.json", "dossier.md", "validation.json"])
def test_load_dossier_package_rejects_non_utf8_members(
    tmp_path: Path,
    research_request_builder,
    evidence_candidate_builder,
    dossier_manifest_builder,
    filename: str,
) -> None:
    _, revision_path = _publish_materialized_package(
        tmp_path / "research",
        research_request_builder(),
        dossier_manifest_builder()["corpus_context"],
        [evidence_candidate_builder()],
    )
    invalid_utf8 = b"\xff\xfe\xfa"
    (revision_path / filename).write_bytes(invalid_utf8)
    if filename != "manifest.json":
        _set_manifest_file_digest(revision_path, filename, invalid_utf8)

    with pytest.raises(ArtifactContractError, match=r"(?i:utf-?8)"):
        _load_dossier_package(revision_path)


@pytest.mark.parametrize(
    ("filename", "mutation", "message"),
    [
        ("manifest.json", "duplicate", "duplicate"),
        ("manifest.json", "version", "schema_version|version"),
        ("manifest.json", "unknown", "unknown|allowlist"),
        ("validation.json", "duplicate", "duplicate"),
        ("validation.json", "version", "schema_version|version"),
        ("validation.json", "unknown", "unknown|allowlist"),
        ("validation.json", "nested_unknown", "unknown|allowlist"),
    ],
)
def test_load_dossier_package_strictly_parses_both_json_envelopes(
    tmp_path: Path,
    research_request_builder,
    evidence_candidate_builder,
    dossier_manifest_builder,
    filename: str,
    mutation: str,
    message: str,
) -> None:
    _, revision_path = _publish_materialized_package(
        tmp_path / "research",
        research_request_builder(),
        dossier_manifest_builder()["corpus_context"],
        [evidence_candidate_builder()],
    )
    target = revision_path / filename
    payload = target.read_bytes()
    if mutation == "duplicate":
        payload = _duplicate_json_field(payload, "schema_version")
    else:
        value = json.loads(payload)
        if mutation == "version":
            value["schema_version"] = "2.0"
        elif mutation == "nested_unknown":
            value["citations"][0]["corpus_path"] = "/private/synthetic"
        else:
            value["unexpected_private_field"] = "synthetic"
        if filename == "manifest.json":
            _retarget_manifest_content(value, revision_path)
            payload = _json_file_bytes(value)
        else:
            payload = _json_file_bytes(value)
    target.write_bytes(payload)
    if filename == "validation.json":
        _set_manifest_file_digest(revision_path, filename, payload)

    with pytest.raises(ArtifactContractError, match=message):
        _load_dossier_package(revision_path)


@pytest.mark.parametrize(
    "field_path",
    [
        "request",
        "request.retrieval",
        "corpus_context",
        "corpus_context.latest_index_runs.embeddings",
        "candidate_evidence.0",
        "candidate_evidence.0.score_components",
        "candidate_evidence.0.citation",
        "curation_operations.0",
        "derived_context.topics.0",
    ],
)
def test_load_dossier_package_rejects_unknown_fields_in_nested_manifest_objects(
    tmp_path: Path,
    research_request_builder,
    evidence_candidate_builder,
    dossier_manifest_builder,
    field_path: str,
) -> None:
    candidate = evidence_candidate_builder()
    context = deepcopy(dossier_manifest_builder()["corpus_context"])
    context["latest_index_runs"] = {
        "embeddings": {
            "run_key": "index-synthetic-1",
            "started_at": "2026-07-12T11:00:00Z",
            "finished_at": "2026-07-12T11:01:00Z",
        }
    }
    package = materialize_dossier_package(
        request=research_request_builder(),
        corpus_context=context,
        candidate_evidence=[candidate],
        derived_context={
            "topics": [
                {
                    "kind": "topic",
                    "topic_key": "topic-synthetic",
                    "label": "Synthetic topic",
                    "document_keys": [candidate["citation"]["document_key"]],
                }
            ],
            "leads": [],
        },
        parent_revision_id="rev-20260711T120000Z-aaaaaaaa",
        curation_operations=[
            {
                "operation": "pin",
                "citation_id": candidate["citation"]["citation_id"],
                "reason": "synthetic owner choice",
                "ordinal": 0,
            }
        ],
        clock=lambda: "2026-07-12T12:00:00Z",
        entropy=lambda: "01234567",
    )
    output_root = tmp_path / "research"
    assert publish_dossier_package(output_root, package) == "created"
    revision_path = _revision_path(output_root, package)
    manifest = deepcopy(package.manifest)
    _nested_object(manifest, field_path)["unexpected_private_field"] = "synthetic"
    _retarget_manifest_content(manifest, revision_path)
    (revision_path / "manifest.json").write_bytes(_json_file_bytes(manifest))

    with pytest.raises(ArtifactContractError, match=r"unknown|allowlist"):
        _load_dossier_package(revision_path)


@pytest.mark.parametrize(
    "corruption",
    [
        "content_digest",
        "dossier_sha256",
        "dossier_bytes",
        "markdown_projection",
        "selected_set",
        "validation_target",
        "initial_validation_claims",
    ],
)
def test_load_dossier_package_rejects_integrity_or_projection_corruption_before_corpus_access(
    tmp_path: Path,
    research_request_builder,
    evidence_candidate_builder,
    dossier_manifest_builder,
    corruption: str,
) -> None:
    _, revision_path = _publish_materialized_package(
        tmp_path / "research",
        research_request_builder(),
        dossier_manifest_builder()["corpus_context"],
        [evidence_candidate_builder()],
    )
    manifest = _read_json_object(revision_path / "manifest.json")
    validation = _read_json_object(revision_path / "validation.json")
    if corruption == "content_digest":
        manifest["content_digest"] = "0" * 64
    elif corruption == "dossier_sha256":
        manifest["files"]["dossier"]["sha256"] = "0" * 64
    elif corruption == "dossier_bytes":
        manifest["files"]["dossier"]["bytes"] += 1
    elif corruption == "markdown_projection":
        dossier_payload = (revision_path / "dossier.md").read_bytes() + b"\nUndeclared appendix.\n"
        (revision_path / "dossier.md").write_bytes(dossier_payload)
        manifest["files"]["dossier"] = _file_digest("dossier.md", dossier_payload)
    elif corruption == "selected_set":
        manifest["candidate_evidence"][0]["selection_state"] = "candidate"
        _retarget_manifest_content(manifest, revision_path, validation=validation)
    elif corruption == "validation_target":
        validation["target_id"] = "rev-20260712T120001Z-deadbeef"
        _write_validation_and_update_manifest(revision_path, manifest, validation)
    else:
        validation.update(
            status="invalid",
            dossier_current=False,
            errors=["synthetic current-corpus failure must not be stored as initial validation"],
        )
        _write_validation_and_update_manifest(revision_path, manifest, validation)
    (revision_path / "manifest.json").write_bytes(_json_file_bytes(manifest))

    corpus_accessed: list[DossierPackage] = []
    with pytest.raises(ArtifactContractError):
        loaded = _load_dossier_package(revision_path)
        corpus_accessed.append(loaded)
    assert corpus_accessed == []


@pytest.mark.skipif(os.name != "posix", reason="V5 targets POSIX filesystem semantics")
def test_load_dossier_package_refuses_symlink_component_and_symlink_member(
    tmp_path: Path,
    research_request_builder,
    evidence_candidate_builder,
    dossier_manifest_builder,
) -> None:
    output_root = tmp_path / "real-research"
    _, revision_path = _publish_materialized_package(
        output_root,
        research_request_builder(),
        dossier_manifest_builder()["corpus_context"],
        [evidence_candidate_builder()],
    )
    linked_root = tmp_path / "linked-research"
    linked_root.symlink_to(output_root, target_is_directory=True)
    linked_revision = linked_root / revision_path.relative_to(output_root)

    with pytest.raises(UnsafeArtifactPathError):
        _load_dossier_package(linked_revision)

    dossier_path = revision_path / "dossier.md"
    outside_copy = tmp_path / "outside-dossier.md"
    outside_copy.write_bytes(dossier_path.read_bytes())
    dossier_path.unlink()
    dossier_path.symlink_to(outside_copy)
    with pytest.raises(UnsafeArtifactPathError):
        _load_dossier_package(revision_path)


def test_child_publication_preserves_explicit_lineage_deterministic_inputs_and_parent_bytes(
    tmp_path: Path,
    research_request_builder,
    citation_builder,
    evidence_candidate_builder,
    dossier_manifest_builder,
) -> None:
    first = evidence_candidate_builder()
    second_excerpt = "Second synthetic candidate promoted by the owner."
    second_citation = citation_builder(
        canonical_id="synthetic-document-2",
        document_key="doc-synthetic-document-2-0123456789ab",
        chunk_key="chunk-synthetic-document-2-0-0123456789ab",
        excerpt=second_excerpt,
        char_end=len(second_excerpt),
    )
    second = evidence_candidate_builder(
        citation=second_citation,
        document_rank=2,
        selection_state="candidate",
        selection_reason="bounded-candidate-pool",
    )
    request = research_request_builder()
    context = dossier_manifest_builder()["corpus_context"]
    parent = _materialize(
        request,
        context,
        [first, second],
        clock="2026-07-12T12:00:00Z",
        entropy="01234567",
    )
    curated = deepcopy([first, second])
    curated[1]["selection_state"] = "selected"
    curated[1]["selection_reason"] = "owner-include"
    operations = [
        {
            "operation": "include",
            "citation_id": second_citation["citation_id"],
            "reason": "add a complementary source",
            "ordinal": 0,
        }
    ]
    child = _materialize(
        request,
        context,
        curated,
        parent_revision_id=parent.manifest["revision_id"],
        curation_operations=operations,
        clock="2026-07-12T12:01:00Z",
        entropy="89abcdef",
    )
    repeated_child = _materialize(
        request,
        context,
        curated,
        parent_revision_id="rev-20260711T120000Z-bbbbbbbb",
        curation_operations=operations,
        clock="2026-07-12T12:02:00Z",
        entropy="fedcba98",
    )

    assert child.manifest["parent_revision_id"] == parent.manifest["revision_id"]
    assert child.manifest["curation_operations"] == operations
    assert child.manifest["content_digest"] != parent.manifest["content_digest"]
    assert child.manifest["content_digest"] == repeated_child.manifest["content_digest"]
    assert _deterministic_validation_inputs(child.validation) == _deterministic_validation_inputs(repeated_child.validation)

    output_root = tmp_path / "research"
    assert publish_dossier_package(output_root, parent) == "created"
    parent_path = _revision_path(output_root, parent)
    original_parent = {path.name: path.read_bytes() for path in parent_path.iterdir()}
    assert publish_dossier_package(output_root, child) == "created"
    assert {path.name: path.read_bytes() for path in parent_path.iterdir()} == original_parent


@pytest.mark.parametrize(
    "mismatch",
    [
        "declared_transition_missing",
        "declared_reason_mismatch",
        "unannounced_state_change",
        "unannounced_reason_change",
        "non_contiguous_ordinal",
    ],
)
def test_materialize_curated_dossier_rejects_operation_log_state_or_reason_mismatch(
    research_request_builder,
    citation_builder,
    evidence_candidate_builder,
    dossier_manifest_builder,
    mismatch: str,
) -> None:
    selected = evidence_candidate_builder()
    included_excerpt = "Synthetic candidate included by the owner."
    included_citation = citation_builder(
        canonical_id="synthetic-curation-candidate",
        document_key="doc-synthetic-curation-candidate-0123456789ab",
        chunk_key="chunk-synthetic-curation-candidate-0-0123456789ab",
        excerpt=included_excerpt,
        char_end=len(included_excerpt),
    )
    candidate = evidence_candidate_builder(
        citation=included_citation,
        document_rank=2,
        selection_state="candidate",
        selection_reason="bounded-candidate-pool",
    )
    parent = _materialize(
        research_request_builder(),
        dossier_manifest_builder()["corpus_context"],
        [selected, candidate],
        clock="2026-07-12T12:00:00Z",
        entropy="01234567",
    )
    child_candidates = deepcopy(parent.manifest["candidate_evidence"])
    child_candidates[1]["selection_state"] = "selected"
    child_candidates[1]["selection_reason"] = "owner-include"
    operations = [
        {
            "operation": "include",
            "citation_id": included_citation["citation_id"],
            "reason": "owner-selected complementary evidence",
            "ordinal": 0,
        }
    ]

    if mismatch == "declared_transition_missing":
        child_candidates[1]["selection_state"] = "candidate"
        child_candidates[1]["selection_reason"] = "bounded-candidate-pool"
    elif mismatch == "declared_reason_mismatch":
        child_candidates[1]["selection_reason"] = "owner-pin"
    elif mismatch == "unannounced_state_change":
        child_candidates[0]["selection_state"] = "pinned"
        child_candidates[0]["selection_reason"] = "owner-pin"
    elif mismatch == "unannounced_reason_change":
        child_candidates[0]["selection_reason"] = "owner-include"
    else:
        operations[0]["ordinal"] = 1

    selected_ids = [
        row["citation"]["citation_id"]
        for state in ("pinned", "selected")
        for row in child_candidates
        if row["selection_state"] == state
    ]
    result = {
        "parent_revision_id": parent.manifest["revision_id"],
        "request": parent.manifest["request"],
        "corpus_context": parent.manifest["corpus_context"],
        "candidate_evidence": child_candidates,
        "selected_citation_ids": selected_ids,
        "curation_operations": operations,
        "derived_context": parent.manifest["derived_context"],
        "status": parent.manifest["status"],
        "includes_drafts": parent.manifest["includes_drafts"],
        "warnings": parent.manifest["warnings"],
        "parent_validation": parent.validation,
    }

    with pytest.raises(ArtifactContractError, match=r"curation|operation|transition|state|reason"):
        materialize_curated_dossier_package(
            parent,
            result,
            clock=lambda: "2026-07-12T12:01:00Z",
            entropy=lambda: "89abcdef",
        )


@pytest.mark.parametrize(
    ("output_kind", "includes_drafts", "unsupported_sections"),
    [("draft", False, 0), ("summary", True, 1)],
)
def test_imported_writing_materialization_matches_schema_and_runtime_contract_for_both_kinds(
    research_request_builder,
    dossier_manifest_builder,
    requested_output_builder,
    handoff_package_builder,
    writing_section_builder,
    writing_output_package_builder,
    output_kind: str,
    includes_drafts: bool,
    unsupported_sections: int,
) -> None:
    request = research_request_builder(visibility="published_and_drafts" if includes_drafts else "published_only")
    dossier = dossier_manifest_builder(request=request)
    handoff = handoff_package_builder(
        dossier_manifest=dossier,
        requested_output=requested_output_builder(kind=output_kind),
    )
    if unsupported_sections:
        content = "## Гипотеза\n\nЭтот раздел явно не подтверждён текущим корпусом."
        sections = [
            writing_section_builder(
                content_markdown=content,
                citation_ids=[],
                unsupported_by_corpus=True,
                unsupported_reason="Синтетическая гипотеза требует дополнительных источников.",
            )
        ]
        writing_output = writing_output_package_builder(
            handoff=handoff,
            content_markdown=content,
            sections=sections,
        )
        validation_warnings = ["unsupported_sections_present"]
    else:
        writing_output = writing_output_package_builder(handoff=handoff)
        validation_warnings = []
    validation = _writing_output_validation(
        writing_output,
        handoff,
        warnings=validation_warnings,
    )

    package = _materialize_imported_writing(
        writing_output,
        handoff,
        validation,
        imported_at="2026-07-12T12:10:00Z",
    )

    imported_type = vars(research_artifacts_module)["ImportedWritingPackage"]
    assert isinstance(package, imported_type)
    schemas = _schemas()
    registry = _registry(schemas)
    checker = FormatChecker()
    Draft202012Validator(
        schemas["imported-writing-manifest.schema.json"],
        registry=registry,
        format_checker=checker,
    ).validate(package.manifest)
    Draft202012Validator(
        schemas["validation-result.schema.json"],
        registry=registry,
        format_checker=checker,
    ).validate(package.validation)
    assert json.loads(package.files["manifest.json"]) == package.manifest
    assert json.loads(package.files["validation.json"]) == package.validation

    expected_identity = canonical_sha256(
        {
            "handoff_id": handoff["handoff_id"],
            "incoming_package_digest": writing_output["package_digest"],
        }
    )
    assert package.manifest == {
        "schema_version": "1.0",
        "artifact_type": "imported_writing",
        "writing_id": f"writing-{expected_identity[:16]}",
        "output_kind": output_kind,
        "incoming_package_digest": writing_output["package_digest"],
        "handoff_id": handoff["handoff_id"],
        "handoff_digest": handoff["package_digest"],
        "dossier_key": handoff["dossier_key"],
        "revision_id": handoff["revision_id"],
        "revision_content_digest": handoff["revision_content_digest"],
        "visibility": handoff["visibility"],
        "includes_drafts": handoff["includes_drafts"],
        "egress_acknowledged": handoff["egress_acknowledged"],
        "draft_evidence_acknowledged": handoff["draft_evidence_acknowledged"],
        "source_created_at": writing_output["created_at"],
        "imported_at": "2026-07-12T12:10:00Z",
        "agent": writing_output["agent"],
        "title": writing_output["title"],
        "content_sha256": writing_output["content_sha256"],
        "validation": {
            "schema_valid": True,
            "package_integrity": True,
            "dossier_current": True,
            "citations_resolved": True,
            "coverage_complete": True,
            "unsupported_sections": unsupported_sections,
        },
        "human_reviewed": False,
        "warnings": [*handoff["warnings"], *validation_warnings],
        "files": package.manifest["files"],
    }
    assert set(package.files) == {"manifest.json", "output.md", "validation.json"}
    assert package.manifest["files"]["output"]["path"] == "output.md"
    assert package.manifest["files"]["validation"]["path"] == "validation.json"
    assert package.validation["status"] == ("valid_with_warnings" if package.manifest["warnings"] else "valid")
    if unsupported_sections:
        assert "unsupported_sections_present" in package.manifest["warnings"]


def test_imported_writing_inherits_privacy_decisions_only_from_validated_handoff(
    research_request_builder,
    dossier_manifest_builder,
    requested_output_builder,
    handoff_package_builder,
    writing_output_package_builder,
) -> None:
    dossier = dossier_manifest_builder(request=research_request_builder(visibility="published_and_drafts"))
    handoff = handoff_package_builder(
        dossier_manifest=dossier,
        requested_output=requested_output_builder(kind="summary"),
        warnings=["dossier_degraded", "exact_evidence_requires_owner_review"],
    )
    writing_output = writing_output_package_builder(handoff=handoff)
    validation = _writing_output_validation(
        writing_output,
        handoff,
        warnings=["structural_coverage_only", "exact_evidence_requires_owner_review"],
    )

    package = _materialize_imported_writing(
        writing_output,
        handoff,
        validation,
        imported_at="2026-07-12T12:10:00Z",
    )

    assert package.manifest["visibility"] == "published_and_drafts"
    assert package.manifest["includes_drafts"] is True
    assert package.manifest["egress_acknowledged"] is True
    assert package.manifest["draft_evidence_acknowledged"] is True
    assert package.manifest["warnings"] == [
        "dossier_degraded",
        "exact_evidence_requires_owner_review",
        "structural_coverage_only",
    ]
    assert package.manifest["human_reviewed"] is False
    assert package.validation["human_reviewed"] is False


def test_imported_markdown_has_generated_boundary_and_separate_source_and_file_hashes(
    handoff_package_builder,
    writing_output_package_builder,
) -> None:
    handoff = handoff_package_builder()
    writing_output = writing_output_package_builder(handoff=handoff)
    package = _materialize_imported_writing(
        writing_output,
        handoff,
        _writing_output_validation(writing_output, handoff),
        imported_at="2026-07-12T12:10:00Z",
    )

    output_bytes = package.files["output.md"]
    output_text = output_bytes.decode("utf-8")
    normalized_banner = output_text[: output_text.index(writing_output["content_markdown"])].casefold()
    assert "generated" in normalized_banner
    assert "source of truth" in normalized_banner
    assert handoff["handoff_id"] in output_text
    assert writing_output["content_markdown"] in output_text
    assert package.manifest["content_sha256"] == hashlib.sha256(writing_output["content_markdown"].encode("utf-8")).hexdigest()
    assert package.manifest["files"]["output"] == _file_digest("output.md", output_bytes)
    assert package.manifest["files"]["output"]["sha256"] != package.manifest["content_sha256"]


def test_imported_validation_retargets_full_automatic_result_without_claiming_human_review(
    handoff_package_builder,
    writing_output_package_builder,
) -> None:
    handoff = handoff_package_builder(warnings=["exact_evidence_requires_owner_review"])
    writing_output = writing_output_package_builder(handoff=handoff)
    incoming_validation = _writing_output_validation(
        writing_output,
        handoff,
        warnings=["structural_coverage_only"],
    )

    package = _materialize_imported_writing(
        writing_output,
        handoff,
        incoming_validation,
        imported_at="2026-07-12T12:10:00Z",
    )

    assert package.validation == {
        **incoming_validation,
        "target_type": "imported_writing",
        "target_id": package.manifest["writing_id"],
        "target_digest": writing_output["package_digest"],
        "warnings": ["exact_evidence_requires_owner_review", "structural_coverage_only"],
        "validated_at": "2026-07-12T12:10:00Z",
    }
    assert package.validation["human_reviewed"] is False


@pytest.mark.skipif(os.name != "posix", reason="V5 targets POSIX filesystem semantics")
def test_imported_writing_publication_is_atomic_owner_only_and_loadable(
    tmp_path: Path,
    handoff_package_builder,
    writing_output_package_builder,
) -> None:
    handoff = handoff_package_builder()
    writing_output = writing_output_package_builder(handoff=handoff)
    package = _materialize_imported_writing(
        writing_output,
        handoff,
        _writing_output_validation(writing_output, handoff),
        imported_at="2026-07-12T12:10:00Z",
    )
    output_root = tmp_path / "research"

    publication = _publish_imported_writing(output_root, package)

    publication_type = vars(research_artifacts_module)["ImportedWritingPublication"]
    expected_path = _imported_writing_path(output_root, package)
    assert isinstance(publication, publication_type)
    assert publication.status == "created"
    assert publication.path == expected_path
    assert publication.package == package
    assert {path.name: path.read_bytes() for path in expected_path.iterdir()} == package.files
    assert stat.S_IMODE(expected_path.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in expected_path.iterdir())

    loaded = _load_imported_writing(expected_path)
    assert loaded == package
    assert set(loaded.files) == {"manifest.json", "output.md", "validation.json"}


def test_identical_reimport_with_different_import_time_reuses_original_stored_package(
    tmp_path: Path,
    handoff_package_builder,
    writing_output_package_builder,
) -> None:
    handoff = handoff_package_builder()
    writing_output = writing_output_package_builder(handoff=handoff)
    validation = _writing_output_validation(writing_output, handoff)
    original = _materialize_imported_writing(
        writing_output,
        handoff,
        validation,
        imported_at="2026-07-12T12:10:00Z",
    )
    repeated = _materialize_imported_writing(
        writing_output,
        handoff,
        validation,
        imported_at="2026-07-13T09:30:00Z",
    )
    output_root = tmp_path / "research"
    first_publication = _publish_imported_writing(output_root, original)
    original_bytes = {path.name: path.read_bytes() for path in first_publication.path.iterdir()}

    repeated_publication = _publish_imported_writing(output_root, repeated)

    assert original.manifest["writing_id"] == repeated.manifest["writing_id"]
    assert original.files != repeated.files
    assert repeated_publication.status == "reused"
    assert repeated_publication.path == first_publication.path
    assert repeated_publication.package == original
    assert repeated_publication.package.manifest["imported_at"] == "2026-07-12T12:10:00Z"
    assert {path.name: path.read_bytes() for path in first_publication.path.iterdir()} == original_bytes


@pytest.mark.skipif(os.name != "posix", reason="V5 targets POSIX filesystem semantics")
def test_imported_writing_publication_rejects_symlink_and_cleans_temporary_directory_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    handoff_package_builder,
    writing_output_package_builder,
) -> None:
    handoff = handoff_package_builder()
    writing_output = writing_output_package_builder(handoff=handoff)
    package = _materialize_imported_writing(
        writing_output,
        handoff,
        _writing_output_validation(writing_output, handoff),
        imported_at="2026-07-12T12:10:00Z",
    )
    real_root = tmp_path / "real-research"
    real_root.mkdir()
    linked_root = tmp_path / "linked-research"
    linked_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(UnsafeArtifactPathError):
        _publish_imported_writing(linked_root, package)

    output_root = tmp_path / "research"

    def fail_atomic_rename(_source: object, _destination: object) -> None:
        raise OSError("synthetic atomic rename failure")

    monkeypatch.setattr(research_artifacts_module.os, "rename", fail_atomic_rename)
    with pytest.raises(OSError, match="synthetic atomic rename failure"):
        _publish_imported_writing(output_root, package)

    final_path = _imported_writing_path(output_root, package)
    assert not final_path.exists()
    if final_path.parent.exists():
        assert not any(path.name.startswith(f".{package.manifest['writing_id']}.") for path in final_path.parent.iterdir())


def test_imported_writing_collision_never_overwrites_existing_bytes(
    tmp_path: Path,
    handoff_package_builder,
    writing_output_package_builder,
) -> None:
    handoff = handoff_package_builder()
    writing_output = writing_output_package_builder(handoff=handoff)
    package = _materialize_imported_writing(
        writing_output,
        handoff,
        _writing_output_validation(writing_output, handoff),
        imported_at="2026-07-12T12:10:00Z",
    )
    publication = _publish_imported_writing(tmp_path / "research", package)
    output_path = publication.path / "output.md"
    collision_bytes = b"synthetic pre-existing collision\n"
    output_path.write_bytes(collision_bytes)

    with pytest.raises(ArtifactCollisionError):
        _publish_imported_writing(tmp_path / "research", package)

    assert output_path.read_bytes() == collision_bytes
    assert not any(path.name.startswith(f".{package.manifest['writing_id']}.") for path in publication.path.parent.iterdir())


@pytest.mark.parametrize("mutation", ["missing", "unknown"])
def test_load_imported_writing_requires_exact_three_file_set(
    tmp_path: Path,
    handoff_package_builder,
    writing_output_package_builder,
    mutation: str,
) -> None:
    package, path = _publish_imported_fixture(
        tmp_path / "research",
        handoff_package_builder,
        writing_output_package_builder,
    )
    if mutation == "missing":
        (path / "output.md").unlink()
    else:
        (path / "agent-notes.txt").write_text("unknown sidecar", encoding="utf-8")

    with pytest.raises(ArtifactContractError):
        _load_imported_writing(path)
    assert package.manifest["writing_id"] in str(path)


@pytest.mark.parametrize(
    "mutation",
    [
        "invalid_json",
        "unknown_manifest",
        "unknown_nested",
        "output_digest",
        "validation_target",
        "validation_status",
    ],
)
def test_load_imported_writing_strictly_rejects_unknown_corrupt_or_digest_mismatched_package(
    tmp_path: Path,
    handoff_package_builder,
    writing_output_package_builder,
    mutation: str,
) -> None:
    _, path = _publish_imported_fixture(
        tmp_path / "research",
        handoff_package_builder,
        writing_output_package_builder,
    )
    manifest_path = path / "manifest.json"
    manifest = _read_json_object(manifest_path)
    if mutation == "invalid_json":
        manifest_path.write_bytes(b"{not-json")
    elif mutation == "unknown_manifest":
        manifest["provider_api_key"] = "synthetic-forbidden-field"
        manifest_path.write_bytes(_json_file_bytes(manifest))
    elif mutation == "unknown_nested":
        manifest["agent"]["local_model_path"] = "/private/synthetic-model"
        manifest_path.write_bytes(_json_file_bytes(manifest))
    elif mutation == "output_digest":
        (path / "output.md").write_text("changed generated output", encoding="utf-8")
    else:
        validation = _read_json_object(path / "validation.json")
        if mutation == "validation_target":
            validation["target_id"] = "writing-0000000000000000"
        else:
            validation["status"] = "valid"
        validation_payload = _json_file_bytes(validation)
        (path / "validation.json").write_bytes(validation_payload)
        manifest["files"]["validation"] = _file_digest("validation.json", validation_payload)
        manifest_path.write_bytes(_json_file_bytes(manifest))

    with pytest.raises(ArtifactContractError):
        _load_imported_writing(path)


@pytest.mark.skipif(os.name != "posix", reason="V5 targets POSIX filesystem semantics")
def test_load_imported_writing_refuses_symlink_member(
    tmp_path: Path,
    handoff_package_builder,
    writing_output_package_builder,
) -> None:
    _, path = _publish_imported_fixture(
        tmp_path / "research",
        handoff_package_builder,
        writing_output_package_builder,
    )
    output_path = path / "output.md"
    outside = tmp_path / "outside.md"
    outside.write_bytes(output_path.read_bytes())
    output_path.unlink()
    output_path.symlink_to(outside)

    with pytest.raises(UnsafeArtifactPathError):
        _load_imported_writing(path)


def _materialize(
    request: dict[str, Any],
    corpus_context: dict[str, Any],
    candidate_evidence: list[dict[str, Any]],
    *,
    clock: str,
    entropy: str,
    parent_revision_id: str | None = None,
    curation_operations: list[dict[str, Any]] | None = None,
    derived_context: dict[str, Any] | None = None,
) -> Any:
    return materialize_dossier_package(
        request=request,
        corpus_context=corpus_context,
        candidate_evidence=candidate_evidence,
        derived_context=derived_context or {"topics": [], "leads": []},
        parent_revision_id=parent_revision_id,
        curation_operations=curation_operations or [],
        clock=lambda: clock,
        entropy=lambda: entropy,
    )


def _publish_materialized_package(
    output_root: Path,
    request: dict[str, Any],
    corpus_context: dict[str, Any],
    candidate_evidence: list[dict[str, Any]],
) -> tuple[DossierPackage, Path]:
    package = _materialize(
        request,
        corpus_context,
        candidate_evidence,
        clock="2026-07-12T12:00:00Z",
        entropy="01234567",
    )
    assert publish_dossier_package(output_root, package) == "created"
    return package, _revision_path(output_root, package)


def _revision_path(output_root: Path, package: DossierPackage) -> Path:
    return output_root / package.manifest["dossier_key"] / "revisions" / package.manifest["revision_id"]


def _load_dossier_package(path: Path) -> DossierPackage:
    loader = cast(Callable[[Path], DossierPackage], vars(research_artifacts_module)["load_dossier_package"])
    return loader(path)


def _read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _json_file_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _file_digest(path: str, payload: bytes) -> dict[str, Any]:
    return {"path": path, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}


def _set_manifest_file_digest(revision_path: Path, filename: str, payload: bytes) -> None:
    manifest_path = revision_path / "manifest.json"
    manifest = _read_json_object(manifest_path)
    label = {"dossier.md": "dossier", "validation.json": "validation"}[filename]
    manifest["files"][label] = _file_digest(filename, payload)
    manifest_path.write_bytes(_json_file_bytes(manifest))


def _duplicate_json_field(payload: bytes, field: str) -> bytes:
    text = payload.decode("utf-8")
    line = next(line for line in text.splitlines() if line.lstrip().startswith(f'"{field}":'))
    return text.replace(line, f"{line}\n{line}", 1).encode("utf-8")


def _nested_object(value: dict[str, Any], field_path: str) -> dict[str, Any]:
    current: Any = value
    for component in field_path.split("."):
        current = current[int(component)] if component.isdigit() else current[component]
    assert isinstance(current, dict)
    return current


def _retarget_manifest_content(
    manifest: dict[str, Any],
    revision_path: Path,
    *,
    validation: dict[str, Any] | None = None,
) -> None:
    manifest["content_digest"] = canonical_sha256(_dossier_content_projection(manifest))
    current_validation = validation or _read_json_object(revision_path / "validation.json")
    current_validation["target_digest"] = manifest["content_digest"]
    _write_validation_and_update_manifest(revision_path, manifest, current_validation)


def _write_validation_and_update_manifest(
    revision_path: Path,
    manifest: dict[str, Any],
    validation: dict[str, Any],
) -> None:
    validation_payload = _json_file_bytes(validation)
    (revision_path / "validation.json").write_bytes(validation_payload)
    manifest["files"]["validation"] = _file_digest("validation.json", validation_payload)


def _dossier_content_projection(manifest: dict[str, Any]) -> dict[str, Any]:
    projection = deepcopy(manifest)
    for field in ("content_digest", "revision_id", "parent_revision_id", "files"):
        projection.pop(field, None)
    context = projection["corpus_context"]
    for field in ("built_at", "latest_import_run_key", "latest_index_runs"):
        context.pop(field, None)
    for candidate in projection["candidate_evidence"]:
        candidate["citation"].pop("import_run_key", None)
    return projection


def _deterministic_validation_inputs(validation: dict[str, Any]) -> dict[str, Any]:
    projection = deepcopy(validation)
    projection.pop("target_id")
    projection.pop("validated_at")
    return projection


def _writing_output_validation(
    writing_output: dict[str, Any],
    handoff: dict[str, Any],
    *,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    effective_warnings = list(warnings or [])
    cited_ids = list(
        dict.fromkeys(citation_id for section in writing_output["sections"] for citation_id in section["citation_ids"])
    )
    return {
        "schema_version": "1.0",
        "artifact_type": "validation_result",
        "target_type": "writing_output",
        "target_id": writing_output["package_digest"],
        "target_digest": writing_output["package_digest"],
        "status": "valid_with_warnings" if effective_warnings else "valid",
        "schema_valid": True,
        "package_integrity": True,
        "dossier_current": True,
        "citations_resolved": True,
        "coverage_complete": True,
        "human_reviewed": False,
        "citations": [
            {"citation_id": citation_id, "status": "valid", "reason": None}
            for citation_id in cited_ids
            if citation_id in handoff["citation_allowlist"]
        ],
        "warnings": effective_warnings,
        "errors": [],
        "validated_at": "2026-07-12T12:09:00Z",
    }


def _materialize_imported_writing(
    writing_output: dict[str, Any],
    handoff: dict[str, Any],
    validation: dict[str, Any],
    *,
    imported_at: str,
) -> Any:
    materializer = cast(
        Callable[..., Any],
        vars(research_artifacts_module)["materialize_imported_writing_package"],
    )
    return materializer(
        writing_output,
        handoff,
        validation,
        imported_at=imported_at,
    )


def _publish_imported_writing(output_root: Path, package: Any) -> Any:
    publisher = cast(
        Callable[[Path, Any], Any],
        vars(research_artifacts_module)["publish_imported_writing_package"],
    )
    return publisher(output_root, package)


def _load_imported_writing(path: Path) -> Any:
    loader = cast(
        Callable[[Path], Any],
        vars(research_artifacts_module)["load_imported_writing_package"],
    )
    return loader(path)


def _imported_writing_path(output_root: Path, package: Any) -> Path:
    return output_root / package.manifest["dossier_key"] / "outputs" / package.manifest["writing_id"]


def _publish_imported_fixture(
    output_root: Path,
    handoff_package_builder: Callable[..., dict[str, Any]],
    writing_output_package_builder: Callable[..., dict[str, Any]],
) -> tuple[Any, Path]:
    handoff = handoff_package_builder()
    writing_output = writing_output_package_builder(handoff=handoff)
    package = _materialize_imported_writing(
        writing_output,
        handoff,
        _writing_output_validation(writing_output, handoff),
        imported_at="2026-07-12T12:10:00Z",
    )
    publication = _publish_imported_writing(output_root, package)
    return package, publication.path
