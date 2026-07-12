from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
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
    parse_strict_object,
    publish_directory_atomic,
    publish_file_atomic,
    safe_http_url,
    validate_output_root,
)
from referencing import Registry, Resource

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
