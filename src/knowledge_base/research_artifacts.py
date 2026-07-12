from __future__ import annotations

import errno
import hashlib
import html
import json
import math
import os
import re
import secrets
import shutil
import stat
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_CITATION_ID_RE = re.compile(r"^cit-[0-9a-f]{16}$")
_HANDOFF_ID_RE = re.compile(r"^handoff-[0-9a-f]{16}$")
_WRITING_ID_RE = re.compile(r"^writing-[0-9a-f]{16}$")
_DOSSIER_KEY_RE = re.compile(r"^research-[a-z0-9_-]+-[0-9a-f]{12}$")
_REVISION_ID_RE = re.compile(r"(?a:^rev-\d{8}T\d{6}Z-[0-9a-f]{8}$)")
_ENTROPY_RE = re.compile(r"^[0-9a-f]{8,}$")
_DATE_RE = re.compile(r"(?a:^\d{4}-\d{2}-\d{2}$)")
_UTC_TIMESTAMP_RE = re.compile(r"(?a:^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$)")
_SHORT_ID_PREFIX_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_SCHEMA_VERSION = "1.0"
_OUTSIDE_GENERATED_WARNING = "output_outside_generated_zone"
_MANIFEST_FILENAME = "manifest.json"
_DOSSIER_FILENAME = "dossier.md"
_WRITING_OUTPUT_FILENAME = "output.md"
_VALIDATION_FILENAME = "validation.json"
_DOSSIER_PACKAGE_FILENAMES = frozenset({_MANIFEST_FILENAME, _DOSSIER_FILENAME, _VALIDATION_FILENAME})
_IMPORTED_WRITING_FILENAMES = frozenset({_MANIFEST_FILENAME, _WRITING_OUTPUT_FILENAME, _VALIDATION_FILENAME})
_MAX_DOSSIER_MEMBER_BYTES = 32 * 1024 * 1024
_UTC_OFFSET = "+00:00"

_REQUEST_FIELDS = (
    "query",
    "source_key",
    "published_from",
    "published_to",
    "visibility",
    "document_limit",
    "fragments_per_document",
    "evidence_limit",
    "candidate_limit",
    "retrieval",
)
_CORPUS_CONTEXT_FIELDS = (
    "database",
    "built_at",
    "embedding_model",
    "embedding_dimension",
    "retrieval_min_similarity",
    "latest_import_run_key",
    "latest_index_runs",
    "git_revision",
    "warnings",
)
_CANDIDATE_FIELDS = (
    "citation",
    "document_rank",
    "fragment_rank",
    "score",
    "score_components",
    "selection_state",
    "selection_reason",
)
_CITATION_FIELDS = (
    "citation_id",
    "identity_sha256",
    "projection_version",
    "source_key",
    "canonical_id",
    "document_key",
    "chunk_key",
    "chunk_ordinal",
    "char_start",
    "char_end",
    "offset_basis",
    "excerpt",
    "excerpt_sha256",
    "title",
    "published_at",
    "document_status",
    "url",
    "raw_snapshot_key",
    "import_run_key",
    "captured_at",
)
_CURATION_FIELDS = ("operation", "citation_id", "reason", "ordinal")
_RETRIEVAL_FIELDS = frozenset(
    {
        "mode",
        "version",
        "lexical_weight",
        "vector_weight",
        "min_similarity",
        "tie_policy",
        "overfetch_factor",
    }
)
_SCORE_COMPONENT_FIELDS = frozenset({"lexical", "vector", "graph_lead"})
_INDEX_TARGETS = frozenset({"embeddings", "related", "communities"})
_INDEX_RUN_FIELDS = frozenset({"run_key", "started_at", "finished_at"})
_DERIVED_ROW_FIELDS = frozenset(
    {
        "kind",
        "topic_key",
        "label",
        "language",
        "description",
        "document_keys",
        "document_statuses",
        "document_key",
        "chunk_key",
        "title",
        "document_status",
        "source_key",
        "published_at",
        "weight",
        "community_key",
        "size",
        "method",
        "top_topics",
        "summary",
        "is_clean",
        "score",
        "grounded_chunk_keys",
    }
)
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "dossier_key",
        "revision_id",
        "parent_revision_id",
        "content_digest",
        "request",
        "corpus_context",
        "candidate_evidence",
        "selected_citation_ids",
        "curation_operations",
        "derived_context",
        "status",
        "includes_drafts",
        "warnings",
        "files",
    }
)
_VALIDATION_FIELDS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "target_type",
        "target_id",
        "target_digest",
        "status",
        "schema_valid",
        "package_integrity",
        "dossier_current",
        "citations_resolved",
        "coverage_complete",
        "human_reviewed",
        "citations",
        "warnings",
        "errors",
        "validated_at",
    }
)
_FILE_DIGEST_FIELDS = frozenset({"path", "sha256", "bytes"})
_VALIDATION_CITATION_FIELDS = ("citation_id", "status", "reason")
_WRITING_OUTPUT_FIELDS = (
    "schema_version",
    "artifact_type",
    "output_kind",
    "handoff_id",
    "handoff_digest",
    "dossier_key",
    "revision_id",
    "visibility",
    "includes_drafts",
    "created_at",
    "agent",
    "title",
    "content_markdown",
    "content_sha256",
    "sections",
    "package_digest",
)
_WRITING_SECTION_FIELDS = (
    "section_id",
    "heading",
    "char_start",
    "char_end",
    "citation_ids",
    "unsupported_by_corpus",
    "unsupported_reason",
)
_WRITING_AGENT_FIELDS = ("name", "model", "run_id")
_WRITING_HANDOFF_FIELDS = (
    "schema_version",
    "artifact_type",
    "handoff_id",
    "identity_sha256",
    "dossier_key",
    "revision_id",
    "revision_content_digest",
    "created_at",
    "visibility",
    "includes_drafts",
    "egress_acknowledged",
    "draft_evidence_acknowledged",
    "query",
    "requested_output",
    "evidence",
    "citation_allowlist",
    "instructions",
    "warnings",
    "package_digest",
)
_REQUESTED_WRITING_OUTPUT_FIELDS = ("kind", "language", "style", "max_words")
_IMPORTED_WRITING_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "writing_id",
        "output_kind",
        "incoming_package_digest",
        "handoff_id",
        "handoff_digest",
        "dossier_key",
        "revision_id",
        "revision_content_digest",
        "visibility",
        "includes_drafts",
        "egress_acknowledged",
        "draft_evidence_acknowledged",
        "source_created_at",
        "imported_at",
        "agent",
        "title",
        "content_sha256",
        "validation",
        "human_reviewed",
        "warnings",
        "files",
    }
)
_IMPORTED_WRITING_SUMMARY_FIELDS = frozenset(
    {
        "schema_valid",
        "package_integrity",
        "dossier_current",
        "citations_resolved",
        "coverage_complete",
        "unsupported_sections",
    }
)

PublishStatus = Literal["created", "reused"]


@dataclass(frozen=True, slots=True)
class DossierPackage:
    manifest: dict[str, Any]
    validation: dict[str, Any]
    markdown: str
    files: dict[str, bytes]


@dataclass(frozen=True, slots=True)
class ImportedWritingPackage:
    manifest: dict[str, Any]
    validation: dict[str, Any]
    markdown: str
    files: dict[str, bytes]


@dataclass(frozen=True, slots=True)
class ImportedWritingPublication:
    status: PublishStatus
    path: Path
    package: ImportedWritingPackage


class ArtifactContractError(ValueError):
    """A serialized artifact violates the supported wire contract."""


class ArtifactCollisionError(FileExistsError):
    """An immutable artifact already exists with different bytes."""


class ShortIdCollisionError(ArtifactCollisionError):
    """Two full digests map to the same shortened external identifier."""


class UnsafeArtifactPathError(ValueError):
    """An artifact path crosses a symlink or another unsafe filesystem boundary."""


class OutputRootAcknowledgementRequired(PermissionError):
    """A write outside data/generated requires an explicit caller acknowledgement."""


class ShortIdRegistry:
    """Create short IDs while retaining full digests for collision detection."""

    def __init__(self, *, prefix: str, length: int = 16) -> None:
        if not _SHORT_ID_PREFIX_RE.fullmatch(prefix):
            raise ValueError("short ID prefix must be lowercase and filesystem-safe")
        if not 1 <= length <= 64:
            raise ValueError("short ID length must be between 1 and 64")
        self.prefix = prefix
        self.length = length
        self._full_digest_by_id: dict[str, str] = {}

    def register(self, full_digest: str) -> str:
        if not _DIGEST_RE.fullmatch(full_digest):
            raise ArtifactContractError("full digest must be 64 lowercase hexadecimal characters")
        short_id = f"{self.prefix}-{full_digest[: self.length]}"
        registered = self._full_digest_by_id.get(short_id)
        if registered is not None and registered != full_digest:
            raise ShortIdCollisionError(f"short ID collision for {short_id}")
        self._full_digest_by_id[short_id] = full_digest
        return short_id


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize one deterministic UTF-8 JSON projection without insignificant whitespace."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def parse_strict_object(
    payload: bytes | str,
    *,
    artifact_type: str,
    required_fields: Iterable[str],
    optional_fields: Iterable[str] = (),
    max_bytes: int,
) -> dict[str, Any]:
    """Parse one bounded versioned JSON object and reject duplicate or unknown fields."""

    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    try:
        encoded = payload.encode("utf-8") if isinstance(payload, str) else payload
    except UnicodeEncodeError as error:
        raise ArtifactContractError("artifact is not valid UTF-8") from error
    if not isinstance(encoded, bytes):
        raise TypeError("payload must be bytes or str")
    if len(encoded) > max_bytes:
        raise ArtifactContractError(f"artifact exceeds {max_bytes} byte limit")

    def reject_constant(value: str) -> Any:
        raise ArtifactContractError(f"non-finite JSON number is forbidden: {value}")

    try:
        decoded = encoded.decode("utf-8")
        value = json.loads(
            decoded,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArtifactContractError("artifact is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ArtifactContractError("artifact must be a JSON object")

    required = {"schema_version", "artifact_type", *required_fields}
    allowed = required | set(optional_fields)
    unknown = sorted(set(value) - allowed)
    missing = sorted(required - set(value))
    if unknown:
        raise ArtifactContractError(f"unknown artifact fields: {', '.join(unknown)}")
    if missing:
        raise ArtifactContractError(f"missing artifact fields: {', '.join(missing)}")
    if value["schema_version"] != _SCHEMA_VERSION:
        raise ArtifactContractError(f"unsupported schema_version: {value['schema_version']!r}")
    if value["artifact_type"] != artifact_type:
        raise ArtifactContractError(f"unexpected artifact_type: {value['artifact_type']!r}")
    return value


def safe_http_url(value: Any) -> str | None:
    """Return a canonical HTTP(S) URL or null without opening or resolving it."""

    if not isinstance(value, str) or not value or len(value) > 4096 or value != value.strip():
        return None
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        return None
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if scheme not in {"http", "https"} or not hostname or parsed.username is not None or parsed.password is not None:
        return None
    if "\\" in parsed.netloc:
        return None

    canonical_host = hostname.lower()
    if ":" in canonical_host:
        canonical_host = f"[{canonical_host}]"
    netloc = canonical_host if port is None else f"{canonical_host}:{port}"
    return urlunsplit((scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def validate_output_root(
    output_root: Path,
    *,
    generated_root: Path,
    acknowledge_unsafe: bool,
) -> str | None:
    """Classify a write root and require acknowledgement outside the generated zone."""

    assert_no_symlink_components(generated_root)
    assert_no_symlink_components(output_root)
    output = _absolute_path(output_root)
    generated = _absolute_path(generated_root)
    if output.is_relative_to(generated):
        return None
    if not acknowledge_unsafe:
        raise OutputRootAcknowledgementRequired(
            f"output root {output} is outside generated zone {generated}; explicit acknowledgement is required"
        )
    return _OUTSIDE_GENERATED_WARNING


def assert_no_symlink_components(path: Path) -> None:
    """Reject a symlink in every currently existing component, including the target."""

    absolute = _absolute_path(path)
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            continue
        except NotADirectoryError as error:
            raise UnsafeArtifactPathError(f"non-directory path component: {current}") from error
        if stat.S_ISLNK(mode):
            raise UnsafeArtifactPathError(f"symlink path component is forbidden: {current}")


def publish_file_atomic(target: Path, payload: bytes) -> PublishStatus:
    """Publish one immutable owner-only file, reusing only byte-identical content."""

    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    destination = _absolute_path(target)
    assert_no_symlink_components(destination)
    _ensure_owner_directory(destination.parent)
    assert_no_symlink_components(destination)
    if os.path.lexists(destination):
        return _reuse_file_or_raise(destination, payload)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        handle = os.fdopen(descriptor, "wb")
        descriptor = -1
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            return _reuse_file_or_raise(destination, payload)
        _fsync_directory(destination.parent)
        return "created"
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        _fsync_directory(destination.parent)


def publish_directory_atomic(target: Path, files: Mapping[str, bytes]) -> PublishStatus:
    """Publish one immutable flat directory package with owner-only files."""

    validated_files = _validated_package_files(files)
    destination = _absolute_path(target)
    assert_no_symlink_components(destination)
    _ensure_owner_directory(destination.parent)
    assert_no_symlink_components(destination)
    if os.path.lexists(destination):
        return _reuse_directory_or_raise(destination, validated_files)

    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent))
    os.chmod(temporary, 0o700)
    try:
        for name, payload in sorted(validated_files.items()):
            _write_owner_file(temporary / name, payload)
        _fsync_directory(temporary)
        assert_no_symlink_components(destination)
        if os.path.lexists(destination):
            return _reuse_directory_or_raise(destination, validated_files)
        try:
            os.rename(temporary, destination)
        except OSError:
            if os.path.lexists(destination):
                return _reuse_directory_or_raise(destination, validated_files)
            raise
        _fsync_directory(destination.parent)
        return "created"
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
            _fsync_directory(destination.parent)


def materialize_dossier_package(
    *,
    request: Any,
    corpus_context: Any,
    candidate_evidence: Sequence[Any],
    derived_context: Any,
    selected_citation_ids: Sequence[str] | None = None,
    warnings: Sequence[str] = (),
    status: Literal["ready", "degraded"] | None = None,
    parent_revision_id: str | None = None,
    curation_operations: Sequence[Any] = (),
    clock: Callable[[], str | datetime] | None = None,
    entropy: Callable[[], str] | None = None,
) -> DossierPackage:
    """Build one cycle-free dossier package fully in memory before publication."""

    request_projection = _project_request(request)
    context_projection = _project_corpus_context(corpus_context)
    candidates = [_project_candidate(value) for value in _bounded_sequence(candidate_evidence, "candidate evidence", 150)]
    selected_ids = _validated_selected_citation_ids(candidates, selected_citation_ids)

    derived_projection = _project_derived_context(derived_context)
    operations = [
        _project_curation_operation(value) for value in _bounded_sequence(curation_operations, "curation operations", 300)
    ]
    if parent_revision_id is not None and not _REVISION_ID_RE.fullmatch(parent_revision_id):
        raise ArtifactContractError("invalid parent dossier revision identifier")

    validated_at, compact_time = _clock_values(clock or _utc_now)
    nonce = (entropy or _random_entropy)()
    if not isinstance(nonce, str) or not _ENTROPY_RE.fullmatch(nonce):
        raise ArtifactContractError("dossier entropy must contain at least eight lowercase hexadecimal characters")
    revision_id = f"rev-{compact_time}-{nonce[:8]}"

    supplied_warnings = _deduplicated_strings(
        [*context_projection["warnings"], *_bounded_strings(warnings, "dossier warnings", 100, 2000)]
    )
    degradation_warnings = [warning for warning in supplied_warnings if warning != "draft_visibility_enabled"]
    if status is not None and status not in {"ready", "degraded"}:
        raise ArtifactContractError("dossier status must be ready, degraded or null")
    includes_drafts = request_projection["visibility"] == "published_and_drafts"
    manifest_warnings = list(supplied_warnings)
    if includes_drafts and "draft_visibility_enabled" not in manifest_warnings:
        manifest_warnings.append("draft_visibility_enabled")
    if len(manifest_warnings) > 100:
        raise ArtifactContractError("combined dossier warnings exceed 100 items")
    effective_status = status or ("degraded" if degradation_warnings else "ready")
    dossier_key = _dossier_key(request_projection)
    manifest: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "artifact_type": "dossier_revision",
        "dossier_key": dossier_key,
        "revision_id": revision_id,
        "parent_revision_id": parent_revision_id,
        "content_digest": "",
        "request": request_projection,
        "corpus_context": context_projection,
        "candidate_evidence": candidates,
        "selected_citation_ids": selected_ids,
        "curation_operations": operations,
        "derived_context": derived_projection,
        "status": effective_status,
        "includes_drafts": includes_drafts,
        "warnings": manifest_warnings,
        "files": {},
    }
    manifest["content_digest"] = canonical_sha256(_dossier_content_projection(manifest))

    markdown = _render_dossier_markdown(manifest)
    validation = _initial_dossier_validation(manifest, validated_at=validated_at)
    _validate_initial_dossier_validation(manifest, validation)
    dossier_bytes = markdown.encode("utf-8")
    validation_bytes = _json_file_bytes(validation)
    manifest["files"] = {
        "dossier": _file_digest(_DOSSIER_FILENAME, dossier_bytes),
        "validation": _file_digest(_VALIDATION_FILENAME, validation_bytes),
    }
    files = {
        _MANIFEST_FILENAME: _json_file_bytes(manifest),
        _DOSSIER_FILENAME: dossier_bytes,
        _VALIDATION_FILENAME: validation_bytes,
    }
    package = DossierPackage(manifest=manifest, validation=validation, markdown=markdown, files=files)
    _validate_dossier_package(package)
    return package


def materialize_curated_dossier_package(
    parent: DossierPackage,
    result: Any,
    clock: Callable[[], str | datetime] | None = None,
    entropy: Callable[[], str] | None = None,
) -> DossierPackage:
    """Materialize one immutable curation child over an already verified parent."""

    _validate_dossier_package(parent)
    parent_manifest = parent.manifest

    parent_revision_id = _result_field(result, "parent_revision_id")
    if parent_revision_id != parent_manifest["revision_id"]:
        raise ArtifactContractError("curation result must target the supplied parent revision")

    request = _project_request(_result_field(result, "request"))
    corpus_context = _project_corpus_context(_result_field(result, "corpus_context"))
    derived_context = _project_derived_context(_result_field(result, "derived_context"))
    if request != parent_manifest["request"]:
        raise ArtifactContractError("curation must preserve the parent research request")
    if corpus_context != parent_manifest["corpus_context"]:
        raise ArtifactContractError("curation must preserve the parent corpus context")
    if derived_context != parent_manifest["derived_context"]:
        raise ArtifactContractError("curation must preserve the parent derived context")

    candidates = [
        _project_candidate(value)
        for value in _bounded_sequence(_result_field(result, "candidate_evidence"), "candidate evidence", 150)
    ]
    _validate_curated_candidate_lineage(parent_manifest["candidate_evidence"], candidates)
    selected_ids = _validated_selected_citation_ids(
        candidates,
        _result_field(result, "selected_citation_ids"),
    )

    operations = [
        _project_curation_operation(value)
        for value in _bounded_sequence(
            _result_field(result, "curation_operations"),
            "curation operations",
            300,
        )
    ]
    if not operations:
        raise ArtifactContractError("curation child requires at least one operation")
    _validate_curated_operation_replay(parent_manifest["candidate_evidence"], candidates, operations)

    status = _result_field(result, "status")
    warnings = _bounded_strings(_result_field(result, "warnings"), "dossier warnings", 100, 2000)
    includes_drafts = _result_field(result, "includes_drafts")
    if status != parent_manifest["status"] or warnings != parent_manifest["warnings"]:
        raise ArtifactContractError("curation must preserve parent status and warnings")
    if includes_drafts is not parent_manifest["includes_drafts"]:
        raise ArtifactContractError("curation must preserve the parent draft scope")
    _validate_curated_parent_gate(parent_manifest, _result_field(result, "parent_validation"))

    return materialize_dossier_package(
        request=request,
        corpus_context=corpus_context,
        candidate_evidence=candidates,
        selected_citation_ids=selected_ids,
        derived_context=derived_context,
        warnings=warnings,
        status=status,
        parent_revision_id=parent_revision_id,
        curation_operations=operations,
        clock=clock,
        entropy=entropy,
    )


def publish_dossier_package(output_root: Path, package: DossierPackage) -> PublishStatus:
    """Atomically publish one immutable three-file dossier revision."""

    _validate_dossier_package(package)
    dossier_key = package.manifest["dossier_key"]
    revision_id = package.manifest["revision_id"]
    target = output_root / dossier_key / "revisions" / revision_id
    return publish_directory_atomic(target, package.files)


def load_dossier_package(revision_dir: Path) -> DossierPackage:
    """Load and fully verify one immutable dossier revision without corpus access."""

    files = _read_dossier_directory(revision_dir)
    try:
        markdown = files[_DOSSIER_FILENAME].decode("utf-8")
    except UnicodeDecodeError as error:
        raise ArtifactContractError("dossier package members must be valid UTF-8") from error

    manifest = parse_strict_object(
        files[_MANIFEST_FILENAME],
        artifact_type="dossier_revision",
        required_fields=_MANIFEST_FIELDS - {"schema_version", "artifact_type"},
        max_bytes=_MAX_DOSSIER_MEMBER_BYTES,
    )
    validation = parse_strict_object(
        files[_VALIDATION_FILENAME],
        artifact_type="validation_result",
        required_fields=_VALIDATION_FIELDS - {"schema_version", "artifact_type"},
        max_bytes=_MAX_DOSSIER_MEMBER_BYTES,
    )
    try:
        _validate_loaded_dossier_manifest(manifest)
        _validate_loaded_initial_validation(validation)
        package = DossierPackage(manifest=manifest, validation=validation, markdown=markdown, files=files)
        _validate_dossier_package(package)
    except ArtifactContractError:
        raise
    except (AttributeError, IndexError, KeyError, TypeError) as error:
        raise ArtifactContractError("dossier package contains invalid field types") from error
    return package


def materialize_imported_writing_package(
    writing_output: Any,
    handoff: Any,
    validation: Any,
    *,
    imported_at: str,
) -> ImportedWritingPackage:
    """Render one validated external writing result into a generated-only package."""

    output = _project_writing_output(writing_output)
    trusted_handoff = _project_import_handoff(handoff)
    automatic_validation = _project_writing_validation(validation, output)
    imported_timestamp = _timestamp(imported_at, "writing import timestamp")
    _validate_import_identity(output, trusted_handoff)

    identity_digest = canonical_sha256(
        {
            "handoff_id": trusted_handoff["handoff_id"],
            "incoming_package_digest": output["package_digest"],
        }
    )
    writing_id = f"writing-{identity_digest[:16]}"
    unsupported_sections = sum(section["unsupported_by_corpus"] is True for section in output["sections"])
    warnings = _deduplicated_strings([*trusted_handoff["warnings"], *automatic_validation["warnings"]])
    if unsupported_sections and "unsupported_sections_present" not in warnings:
        warnings.append("unsupported_sections_present")
    if len(warnings) > 100:
        raise ArtifactContractError("combined imported-writing warnings exceed 100 items")

    manifest: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "artifact_type": "imported_writing",
        "writing_id": writing_id,
        "output_kind": output["output_kind"],
        "incoming_package_digest": output["package_digest"],
        "handoff_id": trusted_handoff["handoff_id"],
        "handoff_digest": trusted_handoff["package_digest"],
        "dossier_key": trusted_handoff["dossier_key"],
        "revision_id": trusted_handoff["revision_id"],
        "revision_content_digest": trusted_handoff["revision_content_digest"],
        "visibility": trusted_handoff["visibility"],
        "includes_drafts": trusted_handoff["includes_drafts"],
        "egress_acknowledged": trusted_handoff["egress_acknowledged"],
        "draft_evidence_acknowledged": trusted_handoff["draft_evidence_acknowledged"],
        "source_created_at": output["created_at"],
        "imported_at": imported_timestamp,
        "agent": output["agent"],
        "title": output["title"],
        "content_sha256": output["content_sha256"],
        "validation": {
            "schema_valid": automatic_validation["schema_valid"],
            "package_integrity": automatic_validation["package_integrity"],
            "dossier_current": automatic_validation["dossier_current"],
            "citations_resolved": automatic_validation["citations_resolved"],
            "coverage_complete": automatic_validation["coverage_complete"],
            "unsupported_sections": unsupported_sections,
        },
        "human_reviewed": False,
        "warnings": warnings,
        "files": {},
    }
    imported_validation = {
        **automatic_validation,
        "target_type": "imported_writing",
        "target_id": writing_id,
        "target_digest": output["package_digest"],
        "status": "valid_with_warnings" if warnings else "valid",
        "human_reviewed": False,
        "warnings": warnings,
        "validated_at": imported_timestamp,
    }
    markdown = _render_imported_writing_markdown(manifest, output["content_markdown"])
    output_bytes = markdown.encode("utf-8")
    validation_bytes = _json_file_bytes(imported_validation)
    manifest["files"] = {
        "output": _file_digest(_WRITING_OUTPUT_FILENAME, output_bytes),
        "validation": _file_digest(_VALIDATION_FILENAME, validation_bytes),
    }
    files = {
        _MANIFEST_FILENAME: _json_file_bytes(manifest),
        _WRITING_OUTPUT_FILENAME: output_bytes,
        _VALIDATION_FILENAME: validation_bytes,
    }
    package = ImportedWritingPackage(
        manifest=manifest,
        validation=imported_validation,
        markdown=markdown,
        files=files,
    )
    _validate_imported_writing_package(package)
    return package


def publish_imported_writing_package(
    output_root: Path,
    package: ImportedWritingPackage,
) -> ImportedWritingPublication:
    """Atomically publish or semantically reuse one immutable generated writing package."""

    _validate_imported_writing_package(package)
    target = _absolute_path(output_root) / package.manifest["dossier_key"] / "outputs" / package.manifest["writing_id"]
    if os.path.lexists(target):
        return _reuse_imported_writing_or_raise(target, package)
    try:
        status = publish_directory_atomic(target, package.files)
    except ArtifactCollisionError:
        if os.path.lexists(target):
            return _reuse_imported_writing_or_raise(target, package)
        raise
    if status == "reused":
        return _reuse_imported_writing_or_raise(target, package)
    return ImportedWritingPublication(status="created", path=target, package=package)


def load_imported_writing_package(package_dir: Path) -> ImportedWritingPackage:
    """Load and fully verify an immutable imported-writing package without corpus access."""

    files = _read_imported_writing_directory(package_dir)
    try:
        markdown = files[_WRITING_OUTPUT_FILENAME].decode("utf-8")
    except UnicodeDecodeError as error:
        raise ArtifactContractError("imported-writing package members must be valid UTF-8") from error
    manifest = parse_strict_object(
        files[_MANIFEST_FILENAME],
        artifact_type="imported_writing",
        required_fields=_IMPORTED_WRITING_MANIFEST_FIELDS - {"schema_version", "artifact_type"},
        max_bytes=_MAX_DOSSIER_MEMBER_BYTES,
    )
    validation = parse_strict_object(
        files[_VALIDATION_FILENAME],
        artifact_type="validation_result",
        required_fields=_VALIDATION_FIELDS - {"schema_version", "artifact_type"},
        max_bytes=_MAX_DOSSIER_MEMBER_BYTES,
    )
    try:
        package = ImportedWritingPackage(
            manifest=manifest,
            validation=validation,
            markdown=markdown,
            files=files,
        )
        _validate_imported_writing_package(package)
    except ArtifactContractError:
        raise
    except (AttributeError, IndexError, KeyError, TypeError) as error:
        raise ArtifactContractError("imported-writing package contains invalid field types") from error
    return package


def _selected_citation_ids(candidates: Sequence[Mapping[str, Any]]) -> list[str]:
    selected_ids = [
        candidate["citation"]["citation_id"]
        for state in ("pinned", "selected")
        for candidate in candidates
        if candidate["selection_state"] == state
    ]
    if not selected_ids:
        raise ArtifactContractError("dossier requires at least one selected evidence citation")
    if len(selected_ids) > 100 or len(selected_ids) != len(set(selected_ids)):
        raise ArtifactContractError("selected evidence must contain at most 100 unique citations")
    return selected_ids


def _validated_selected_citation_ids(
    candidates: Sequence[Mapping[str, Any]],
    selected_citation_ids: Sequence[str] | None,
) -> list[str]:
    evidence_ids = _selected_citation_ids(candidates)
    if selected_citation_ids is None:
        return evidence_ids

    selected_ids = _bounded_strings(selected_citation_ids, "selected citation IDs", 100, 20)
    if not selected_ids or len(selected_ids) != len(set(selected_ids)):
        raise ArtifactContractError("selected evidence must contain 1..100 unique citations")
    if any(not _CITATION_ID_RE.fullmatch(citation_id) for citation_id in selected_ids):
        raise ArtifactContractError("invalid selected citation ID")
    if selected_ids != evidence_ids:
        raise ArtifactContractError("selected citations must present pinned then selected evidence in stable candidate order")
    return selected_ids


def _validate_curated_candidate_lineage(
    parent_candidates: Sequence[Mapping[str, Any]],
    child_candidates: Sequence[Mapping[str, Any]],
) -> None:
    if len(parent_candidates) != len(child_candidates):
        raise ArtifactContractError("curation must preserve the exact parent candidate universe")
    mutable_fields = {"selection_state", "selection_reason"}
    for parent, child in zip(parent_candidates, child_candidates, strict=True):
        parent_identity = {key: value for key, value in parent.items() if key not in mutable_fields}
        child_identity = {key: value for key, value in child.items() if key not in mutable_fields}
        if parent_identity != child_identity:
            raise ArtifactContractError("curation must preserve parent candidate order and evidence")


def _validate_curated_operation_replay(
    parent_candidates: Sequence[Mapping[str, Any]],
    child_candidates: Sequence[Mapping[str, Any]],
    operations: Sequence[Mapping[str, Any]],
) -> None:
    if [operation["ordinal"] for operation in operations] != list(range(len(operations))):
        raise ArtifactContractError("curation operation ordinals must be contiguous and ordered from zero")

    candidate_index: dict[str, int] = {}
    expected_state_and_reason: list[tuple[str, str]] = []
    for index, candidate in enumerate(parent_candidates):
        citation_id = candidate["citation"]["citation_id"]
        if citation_id in candidate_index:
            raise ArtifactContractError("curation parent candidate IDs must be unique")
        candidate_index[citation_id] = index
        expected_state_and_reason.append((candidate["selection_state"], candidate["selection_reason"]))

    seen_targets: set[str] = set()
    transitions = {
        "include": ({"candidate", "excluded"}, "selected"),
        "exclude": ({"selected", "pinned"}, "excluded"),
        "pin": ({"selected"}, "pinned"),
    }
    for operation in operations:
        citation_id = operation["citation_id"]
        if citation_id in seen_targets:
            raise ArtifactContractError("curation operation targets must be unique")
        seen_targets.add(citation_id)
        target_index = candidate_index.get(citation_id)
        if target_index is None:
            raise ArtifactContractError("curation operation target must exist in the parent candidate universe")

        operation_name = operation["operation"]
        allowed_states, next_state = transitions[operation_name]
        current_state, _ = expected_state_and_reason[target_index]
        if current_state not in allowed_states:
            raise ArtifactContractError(f"curation {operation_name} operation is invalid from {current_state} state")
        expected_state_and_reason[target_index] = (next_state, f"owner-{operation_name}")

    for child, expected in zip(child_candidates, expected_state_and_reason, strict=True):
        actual = (child["selection_state"], child["selection_reason"])
        if actual != expected:
            raise ArtifactContractError("curation child state/reason does not match the ordered operation replay")


def _validate_curated_parent_gate(parent_manifest: Mapping[str, Any], validation: Any) -> None:
    required_true = (
        "schema_valid",
        "package_integrity",
        "dossier_current",
        "citations_resolved",
        "coverage_complete",
    )
    if (
        _result_field(validation, "target_type") != "dossier_revision"
        or _result_field(validation, "target_id") != parent_manifest["revision_id"]
        or _result_field(validation, "target_digest") != parent_manifest["content_digest"]
        or _result_field(validation, "status") not in {"valid", "valid_with_warnings"}
        or any(_result_field(validation, field) is not True for field in required_true)
    ):
        raise ArtifactContractError("curation requires a current, resolved parent validation")


def _result_field(value: Any, field: str) -> Any:
    if isinstance(value, Mapping):
        if field not in value:
            raise ArtifactContractError(f"curation result is missing {field}")
        return value[field]
    try:
        return getattr(value, field)
    except AttributeError as error:
        raise ArtifactContractError(f"curation result is missing {field}") from error


def _project_writing_output(value: Any) -> dict[str, Any]:
    raw = _project_fields(value, _WRITING_OUTPUT_FIELDS, "writing output")
    if raw["schema_version"] != _SCHEMA_VERSION or raw["artifact_type"] != "writing_output":
        raise ArtifactContractError("unsupported writing-output envelope")
    output_kind = _enum_value(raw["output_kind"])
    if output_kind not in {"draft", "summary"}:
        raise ArtifactContractError("unsupported writing output kind")
    handoff_id = _bounded_string(raw["handoff_id"], "writing output handoff_id", 1, 24)
    if not _HANDOFF_ID_RE.fullmatch(handoff_id):
        raise ArtifactContractError("invalid writing output handoff_id")
    handoff_digest = _required_digest(raw["handoff_digest"], "writing output handoff digest")
    dossier_key = _bounded_string(raw["dossier_key"], "writing output dossier_key", 1, 100)
    revision_id = _bounded_string(raw["revision_id"], "writing output revision_id", 1, 32)
    if not _DOSSIER_KEY_RE.fullmatch(dossier_key) or not _REVISION_ID_RE.fullmatch(revision_id):
        raise ArtifactContractError("invalid writing output dossier identity")
    includes_drafts = raw["includes_drafts"]
    if not isinstance(includes_drafts, bool):
        raise ArtifactContractError("writing output includes_drafts must be a boolean")
    visibility = _enum_value(raw["visibility"])
    expected_visibility = "published_and_drafts" if includes_drafts else "published_only"
    if visibility != expected_visibility:
        raise ArtifactContractError("writing output visibility does not match its draft scope")
    agent = _project_writing_agent(raw["agent"])
    title = _bounded_string(raw["title"], "writing output title", 1, 500)
    content = _bounded_string(raw["content_markdown"], "writing output content", 1, 1_048_576)
    content_digest = _required_digest(raw["content_sha256"], "writing output content digest")
    if content_digest != hashlib.sha256(content.encode("utf-8")).hexdigest():
        raise ArtifactContractError("writing output content digest mismatch")
    sections = [
        _project_writing_section(section, content_length=len(content))
        for section in _bounded_sequence(raw["sections"], "writing output sections", 200)
    ]
    if not sections:
        raise ArtifactContractError("writing output requires at least one section")
    package_digest = _required_digest(raw["package_digest"], "writing output package digest")
    output = {
        "schema_version": _SCHEMA_VERSION,
        "artifact_type": "writing_output",
        "output_kind": output_kind,
        "handoff_id": handoff_id,
        "handoff_digest": handoff_digest,
        "dossier_key": dossier_key,
        "revision_id": revision_id,
        "visibility": visibility,
        "includes_drafts": includes_drafts,
        "created_at": _timestamp(raw["created_at"], "writing output source timestamp"),
        "agent": agent,
        "title": title,
        "content_markdown": content,
        "content_sha256": content_digest,
        "sections": sections,
        "package_digest": package_digest,
    }
    digest_projection = dict(output)
    digest_projection.pop("package_digest")
    if canonical_sha256(digest_projection) != package_digest:
        raise ArtifactContractError("writing output package digest mismatch")
    return output


def _project_writing_agent(value: Any) -> dict[str, str | None]:
    raw = _project_fields(value, _WRITING_AGENT_FIELDS, "writing agent metadata")
    agent: dict[str, str | None] = {}
    for field in _WRITING_AGENT_FIELDS:
        item = raw[field]
        if item is not None and (not isinstance(item, str) or len(item) > 500):
            raise ArtifactContractError(f"writing agent {field} must contain at most 500 characters or null")
        agent[field] = item
    return agent


def _project_writing_section(value: Any, *, content_length: int) -> dict[str, Any]:
    raw = _project_fields(value, _WRITING_SECTION_FIELDS, "writing section")
    section_id = _bounded_string(raw["section_id"], "writing section_id", 9, 108)
    if re.fullmatch(r"section-[A-Za-z0-9_-]{1,100}", section_id) is None:
        raise ArtifactContractError("invalid writing section_id")
    heading = raw["heading"]
    if heading is not None and (not isinstance(heading, str) or len(heading) > 500):
        raise ArtifactContractError("writing section heading must contain at most 500 characters or null")
    start = _bounded_integer(raw["char_start"], "writing section char_start", 0)
    end = _bounded_integer(raw["char_end"], "writing section char_end", 1)
    if start >= end or end > content_length:
        raise ArtifactContractError("writing section range is outside content")
    citation_ids = _bounded_strings(raw["citation_ids"], "writing section citations", 50, 20)
    if len(citation_ids) != len(set(citation_ids)) or any(
        not _CITATION_ID_RE.fullmatch(citation_id) for citation_id in citation_ids
    ):
        raise ArtifactContractError("writing section citations must be unique valid citation IDs")
    unsupported = raw["unsupported_by_corpus"]
    if not isinstance(unsupported, bool):
        raise ArtifactContractError("writing section unsupported flag must be a boolean")
    reason = raw["unsupported_reason"]
    if unsupported:
        reason = _bounded_string(reason, "writing section unsupported reason", 1, 2000)
    elif not citation_ids or reason is not None:
        raise ArtifactContractError("supported writing sections require citations and no unsupported reason")
    return {
        "section_id": section_id,
        "heading": heading,
        "char_start": start,
        "char_end": end,
        "citation_ids": citation_ids,
        "unsupported_by_corpus": unsupported,
        "unsupported_reason": reason,
    }


def _project_import_handoff(value: Any) -> dict[str, Any]:
    raw = _project_fields(value, _WRITING_HANDOFF_FIELDS, "writing handoff")
    if raw["schema_version"] != _SCHEMA_VERSION or raw["artifact_type"] != "writing_handoff":
        raise ArtifactContractError("unsupported writing handoff envelope")
    handoff_id = _bounded_string(raw["handoff_id"], "writing handoff_id", 1, 24)
    if not _HANDOFF_ID_RE.fullmatch(handoff_id):
        raise ArtifactContractError("invalid writing handoff_id")
    _required_digest(raw["identity_sha256"], "writing handoff identity digest")
    package_digest = _required_digest(raw["package_digest"], "writing handoff package digest")
    revision_digest = _required_digest(raw["revision_content_digest"], "writing handoff revision digest")
    dossier_key = _bounded_string(raw["dossier_key"], "writing handoff dossier_key", 1, 100)
    revision_id = _bounded_string(raw["revision_id"], "writing handoff revision_id", 1, 32)
    if not _DOSSIER_KEY_RE.fullmatch(dossier_key) or not _REVISION_ID_RE.fullmatch(revision_id):
        raise ArtifactContractError("invalid writing handoff dossier identity")
    includes_drafts = raw["includes_drafts"]
    if not isinstance(includes_drafts, bool):
        raise ArtifactContractError("writing handoff includes_drafts must be a boolean")
    visibility = _enum_value(raw["visibility"])
    expected_visibility = "published_and_drafts" if includes_drafts else "published_only"
    if visibility != expected_visibility:
        raise ArtifactContractError("writing handoff visibility does not match its draft scope")
    if raw["egress_acknowledged"] is not True:
        raise ArtifactContractError("writing handoff requires external-disclosure acknowledgement")
    expected_draft_ack = includes_drafts
    if raw["draft_evidence_acknowledged"] is not expected_draft_ack:
        raise ArtifactContractError("writing handoff draft acknowledgement does not match its scope")
    requested = _project_fields(raw["requested_output"], _REQUESTED_WRITING_OUTPUT_FIELDS, "requested writing output")
    requested_kind = _enum_value(requested["kind"])
    if requested_kind not in {"draft", "summary"}:
        raise ArtifactContractError("unsupported requested writing output kind")
    warnings = _bounded_strings(raw["warnings"], "writing handoff warnings", 100, 2000)
    return {
        "handoff_id": handoff_id,
        "package_digest": package_digest,
        "dossier_key": dossier_key,
        "revision_id": revision_id,
        "revision_content_digest": revision_digest,
        "visibility": visibility,
        "includes_drafts": includes_drafts,
        "egress_acknowledged": True,
        "draft_evidence_acknowledged": expected_draft_ack,
        "requested_output_kind": requested_kind,
        "warnings": warnings,
    }


def _project_writing_validation(value: Any, output: Mapping[str, Any]) -> dict[str, Any]:
    raw = _project_fields(value, tuple(_VALIDATION_FIELDS), "writing validation")
    validation = {field: _copy_json(_enum_value(raw[field])) for field in _VALIDATION_FIELDS}
    if validation["schema_version"] != _SCHEMA_VERSION or validation["artifact_type"] != "validation_result":
        raise ArtifactContractError("unsupported writing validation envelope")
    if (
        validation["target_type"] != "writing_output"
        or validation["target_id"] != output["package_digest"]
        or validation["target_digest"] != output["package_digest"]
    ):
        raise ArtifactContractError("writing validation targets another output package")
    if validation["status"] not in {"valid", "valid_with_warnings"}:
        raise ArtifactContractError("writing output must pass automatic validation before materialization")
    claims = ("schema_valid", "package_integrity", "dossier_current", "citations_resolved", "coverage_complete")
    if any(validation[claim] is not True for claim in claims) or validation["human_reviewed"] is not False:
        raise ArtifactContractError("writing validation automatic claims are inconsistent")
    citations = _bounded_sequence(validation["citations"], "writing validation citations", 250)
    projected_citations: list[dict[str, Any]] = []
    for item in citations:
        citation = _project_fields(item, _VALIDATION_CITATION_FIELDS, "writing validation citation")
        if not isinstance(citation["citation_id"], str) or not _CITATION_ID_RE.fullmatch(citation["citation_id"]):
            raise ArtifactContractError("invalid writing validation citation ID")
        if citation["status"] != "valid" or citation["reason"] is not None:
            raise ArtifactContractError("accepted writing validation citations must be current")
        projected_citations.append(dict(citation))
    validation["citations"] = projected_citations
    validation["warnings"] = _bounded_strings(validation["warnings"], "writing validation warnings", 100, 2000)
    validation["errors"] = _bounded_strings(validation["errors"], "writing validation errors", 100, 2000)
    if validation["errors"]:
        raise ArtifactContractError("accepted writing validation cannot contain errors")
    validation["validated_at"] = _timestamp(validation["validated_at"], "writing validation timestamp")
    return validation


def _validate_import_identity(output: Mapping[str, Any], handoff: Mapping[str, Any]) -> None:
    if (
        output["handoff_id"] != handoff["handoff_id"]
        or output["handoff_digest"] != handoff["package_digest"]
        or output["dossier_key"] != handoff["dossier_key"]
        or output["revision_id"] != handoff["revision_id"]
        or output["visibility"] != handoff["visibility"]
        or output["includes_drafts"] is not handoff["includes_drafts"]
        or output["output_kind"] != handoff["requested_output_kind"]
    ):
        raise ArtifactContractError("writing output does not match the validated handoff")


def _required_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ArtifactContractError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _project_request(value: Any) -> dict[str, Any]:
    raw = _project_fields(value, _REQUEST_FIELDS, "research request")
    query = raw["query"]
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 1000:
        raise ArtifactContractError("research request query must contain 1..1000 characters")
    source_key = raw["source_key"]
    if source_key is not None and (not isinstance(source_key, str) or not source_key):
        raise ArtifactContractError("research request source_key must be a non-empty string or null")
    visibility = _enum_value(raw["visibility"])
    if visibility not in {"published_only", "published_and_drafts"}:
        raise ArtifactContractError("unsupported research request visibility")
    retrieval = _allowlisted_mapping(raw["retrieval"], _RETRIEVAL_FIELDS, "research retrieval")
    published_from = _calendar_date(raw["published_from"], "published_from")
    published_to = _calendar_date(raw["published_to"], "published_to")
    if published_from is not None and published_to is not None and published_from > published_to:
        raise ArtifactContractError("published_from must not be later than published_to")
    projection = {
        "query": query.strip(),
        "source_key": source_key,
        "published_from": published_from.isoformat() if published_from is not None else None,
        "published_to": published_to.isoformat() if published_to is not None else None,
        "visibility": visibility,
        "document_limit": _bounded_integer(raw["document_limit"], "document_limit", 1, 50),
        "fragments_per_document": _bounded_integer(raw["fragments_per_document"], "fragments_per_document", 1, 5),
        "evidence_limit": _bounded_integer(raw["evidence_limit"], "evidence_limit", 1, 100),
        "candidate_limit": _bounded_integer(raw["candidate_limit"], "candidate_limit", 1, 150),
        "retrieval": retrieval,
    }
    if projection["evidence_limit"] > projection["candidate_limit"]:
        raise ArtifactContractError("evidence_limit must not exceed candidate_limit")
    return projection


def _project_corpus_context(value: Any) -> dict[str, Any]:
    raw = _project_fields(value, _CORPUS_CONTEXT_FIELDS, "corpus context")
    database = raw["database"]
    model = raw["embedding_model"]
    if not isinstance(database, str) or not database or not isinstance(model, str) or not model:
        raise ArtifactContractError("corpus context database and embedding model must be non-empty strings")
    similarity = raw["retrieval_min_similarity"]
    if isinstance(similarity, bool) or not isinstance(similarity, int | float) or not math.isfinite(similarity):
        raise ArtifactContractError("corpus context similarity must be a finite number")
    if not -1 <= similarity <= 1:
        raise ArtifactContractError("corpus context similarity must be within -1..1")
    projection = {
        "database": database,
        "built_at": _timestamp(raw["built_at"], "corpus context built_at"),
        "embedding_model": model,
        "embedding_dimension": _bounded_integer(raw["embedding_dimension"], "embedding_dimension", 1),
        "retrieval_min_similarity": float(similarity),
        "latest_import_run_key": _optional_string(raw["latest_import_run_key"], "latest_import_run_key"),
        "latest_index_runs": _project_index_runs(raw["latest_index_runs"]),
        "git_revision": _optional_string(raw["git_revision"], "git_revision"),
        "warnings": _bounded_strings(raw["warnings"], "corpus context warnings", 100, 2000),
    }
    return projection


def _project_index_runs(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ArtifactContractError("latest_index_runs must be an object")
    unknown = set(value) - _INDEX_TARGETS
    if unknown:
        raise ArtifactContractError(f"unknown index run targets: {', '.join(sorted(unknown))}")
    result: dict[str, Any] = {}
    for target, raw_run in value.items():
        run = _allowlisted_mapping(raw_run, _INDEX_RUN_FIELDS, f"{target} index run", require_all=False)
        result[target] = run
    return result


def _project_candidate(value: Any) -> dict[str, Any]:
    raw = _project_fields(value, _CANDIDATE_FIELDS, "evidence candidate")
    score = raw["score"]
    if isinstance(score, bool) or not isinstance(score, int | float) or not math.isfinite(score):
        raise ArtifactContractError("evidence candidate score must be finite")
    components = _allowlisted_mapping(raw["score_components"], _SCORE_COMPONENT_FIELDS, "score components")
    for component in components.values():
        if component is not None and (
            isinstance(component, bool) or not isinstance(component, int | float) or not math.isfinite(component)
        ):
            raise ArtifactContractError("score components must be finite numbers or null")
    selection_state = _enum_value(raw["selection_state"])
    if selection_state not in {"candidate", "selected", "pinned", "excluded"}:
        raise ArtifactContractError("unsupported evidence selection state")
    reason = raw["selection_reason"]
    if not isinstance(reason, str) or not 1 <= len(reason) <= 500:
        raise ArtifactContractError("selection reason must contain 1..500 characters")
    return {
        "citation": _project_citation(raw["citation"]),
        "document_rank": _bounded_integer(raw["document_rank"], "document_rank", 1),
        "fragment_rank": _bounded_integer(raw["fragment_rank"], "fragment_rank", 1),
        "score": float(score),
        "score_components": components,
        "selection_state": selection_state,
        "selection_reason": reason,
    }


def _project_citation(value: Any) -> dict[str, Any]:
    raw = _project_fields(value, _CITATION_FIELDS, "citation")
    citation = {field: _copy_json(_enum_value(raw[field])) for field in _CITATION_FIELDS}
    citation_id = citation["citation_id"]
    identity = citation["identity_sha256"]
    excerpt = citation["excerpt"]
    excerpt_digest = citation["excerpt_sha256"]
    _validate_citation_identifiers(citation_id, identity)
    _bounded_string(citation["source_key"], "citation source_key", 1, 256)
    _bounded_string(citation["canonical_id"], "citation canonical_id", 1, 1024)
    _bounded_string(citation["document_key"], "citation document_key", 1, 256)
    _bounded_string(citation["chunk_key"], "citation chunk_key", 1, 256)
    citation["chunk_ordinal"] = _bounded_integer(citation["chunk_ordinal"], "citation chunk_ordinal", 0)
    _bounded_string(citation["title"], "citation title", 0, 2000)
    citation["published_at"] = _optional_timestamp(citation["published_at"], "citation published_at")
    citation["captured_at"] = _optional_timestamp(citation["captured_at"], "citation captured_at")
    citation["raw_snapshot_key"] = _optional_bounded_string(citation["raw_snapshot_key"], "citation raw_snapshot_key", 256)
    citation["import_run_key"] = _optional_bounded_string(citation["import_run_key"], "citation import_run_key", 256)
    if not isinstance(excerpt, str) or not 1 <= len(excerpt) <= 20_000:
        raise ArtifactContractError("citation excerpt must contain 1..20000 characters")
    if not isinstance(excerpt_digest, str) or excerpt_digest != hashlib.sha256(excerpt.encode()).hexdigest():
        raise ArtifactContractError("citation excerpt digest does not match exact excerpt")
    start = _bounded_integer(citation["char_start"], "citation char_start", 0)
    end = _bounded_integer(citation["char_end"], "citation char_end", 1)
    if start >= end or end - start != len(excerpt):
        raise ArtifactContractError("citation offsets must cover the exact excerpt")
    if citation["projection_version"] != "citation-v1" or citation["offset_basis"] != "normalized_whitespace_v1":
        raise ArtifactContractError("unsupported citation projection")
    if citation["document_status"] not in {"published", "draft"}:
        raise ArtifactContractError("unsupported citation document status")
    if citation["url"] is not None and safe_http_url(citation["url"]) != citation["url"]:
        raise ArtifactContractError("citation URL must be canonical credential-free HTTP(S)")
    identity_projection = {
        "projection_version": citation["projection_version"],
        "source_key": citation["source_key"],
        "canonical_id": citation["canonical_id"],
        "document_key": citation["document_key"],
        "chunk_key": citation["chunk_key"],
        "char_start": start,
        "char_end": end,
        "offset_basis": citation["offset_basis"],
        "excerpt_sha256": excerpt_digest,
    }
    expected_identity = canonical_sha256(identity_projection)
    if identity != expected_identity or citation_id != f"cit-{expected_identity[:16]}":
        raise ArtifactContractError("citation identity does not match its exact projection")
    return citation


def _validate_citation_identifiers(citation_id: Any, identity: Any) -> None:
    if not isinstance(citation_id, str) or not _CITATION_ID_RE.fullmatch(citation_id):
        raise ArtifactContractError("invalid citation_id")
    if not isinstance(identity, str) or not _DIGEST_RE.fullmatch(identity):
        raise ArtifactContractError("invalid citation identity digest")


def _project_curation_operation(value: Any) -> dict[str, Any]:
    raw = _project_fields(value, _CURATION_FIELDS, "curation operation")
    operation = _enum_value(raw["operation"])
    if operation not in {"include", "exclude", "pin"}:
        raise ArtifactContractError("unsupported curation operation")
    citation_id = raw["citation_id"]
    if not isinstance(citation_id, str) or not _CITATION_ID_RE.fullmatch(citation_id):
        raise ArtifactContractError("invalid curation citation_id")
    reason = raw["reason"]
    if reason is not None and (not isinstance(reason, str) or len(reason) > 500):
        raise ArtifactContractError("curation reason must contain at most 500 characters")
    return {
        "operation": operation,
        "citation_id": citation_id,
        "reason": reason,
        "ordinal": _bounded_integer(raw["ordinal"], "curation ordinal", 0),
    }


def _project_derived_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"topics", "leads"}:
        raise ArtifactContractError("derived context must contain only topics and leads")
    projection: dict[str, Any] = {}
    for name in ("topics", "leads"):
        rows = _bounded_sequence(value[name], f"derived context {name}", 100)
        projection[name] = [
            _allowlisted_mapping(row, _DERIVED_ROW_FIELDS, f"derived context {name} row", require_all=False) for row in rows
        ]
    return projection


def _dossier_key(request: Mapping[str, Any]) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", str(request["query"]).strip().lower()).strip("-_") or "topic"
    return f"research-{slug[:64]}-{canonical_sha256(request)[:12]}"


def _dossier_content_projection(manifest: Mapping[str, Any]) -> dict[str, Any]:
    projection = _copy_json(manifest)
    assert isinstance(projection, dict)
    for field in ("content_digest", "revision_id", "parent_revision_id", "files"):
        projection.pop(field, None)
    context = projection["corpus_context"]
    for field in ("built_at", "latest_import_run_key", "latest_index_runs"):
        context.pop(field, None)
    for candidate in projection["candidate_evidence"]:
        candidate["citation"].pop("import_run_key", None)
    return projection


def _render_dossier_markdown(manifest: Mapping[str, Any]) -> str:
    selected = set(manifest["selected_citation_ids"])
    citations = {
        candidate["citation"]["citation_id"]: candidate["citation"]
        for candidate in manifest["candidate_evidence"]
        if candidate["citation"]["citation_id"] in selected
    }
    lines = [
        "# Исследовательское досье",
        "",
        f"Запрос: {_safe_markdown_inline(manifest['request']['query'])}",
        "",
    ]
    if "draft_visibility_enabled" in manifest["warnings"]:
        detail = ": в эту ревизию явно включены черновики." if manifest["includes_drafts"] else ""
        lines.extend((f"> ⚠️ draft_visibility_enabled{detail}", ""))
    for warning in manifest["warnings"]:
        if warning == "draft_visibility_enabled":
            continue
        lines.extend((f"> Предупреждение: {_safe_markdown_inline(warning)}", ""))
    for citation_id in manifest["selected_citation_ids"]:
        citation = citations[citation_id]
        lines.extend(
            (
                f"## {citation_id}",
                "",
                f"Заголовок: {_safe_markdown_inline(citation['title'])}",
                f"Источник: {_safe_markdown_inline(citation['source_key'])}",
                f"Статус: {_safe_markdown_inline(citation['document_status'])}",
                "",
                "Фрагмент:",
                *_safe_markdown_quote(citation["excerpt"]),
                "",
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def _render_imported_writing_markdown(manifest: Mapping[str, Any], content: str) -> str:
    return _imported_writing_markdown_prefix(manifest) + content


def _imported_writing_markdown_prefix(manifest: Mapping[str, Any]) -> str:
    return (
        "<!-- GENERATED OUTPUT: not a source of truth -->\n"
        "> **Generated output — not a source of truth.**\n"
        f"> Kind: `{manifest['output_kind']}`; handoff: `{manifest['handoff_id']}`; "
        f"dossier revision: `{manifest['revision_id']}`.\n\n"
    )


def _validate_imported_writing_package(package: ImportedWritingPackage) -> None:
    if not isinstance(package, ImportedWritingPackage):
        raise TypeError("package must be an ImportedWritingPackage")
    if set(package.files) != _IMPORTED_WRITING_FILENAMES:
        raise ArtifactContractError("imported-writing package must contain exactly three files")
    if set(package.manifest) != _IMPORTED_WRITING_MANIFEST_FIELDS or set(package.validation) != _VALIDATION_FIELDS:
        raise ArtifactContractError("imported-writing package fields do not match the contract allowlist")
    try:
        serialized_manifest = json.loads(package.files[_MANIFEST_FILENAME])
        serialized_validation = json.loads(package.files[_VALIDATION_FILENAME])
        serialized_markdown = package.files[_WRITING_OUTPUT_FILENAME].decode("utf-8")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArtifactContractError("imported-writing files must be valid UTF-8 JSON/Markdown") from error
    if serialized_manifest != package.manifest or serialized_validation != package.validation:
        raise ArtifactContractError("imported-writing object and serialized JSON disagree")
    if serialized_markdown != package.markdown:
        raise ArtifactContractError("imported-writing object and serialized Markdown disagree")

    _validate_loaded_imported_writing_manifest(package.manifest)
    _validate_loaded_imported_writing_validation(package.manifest, package.validation)
    prefix = _imported_writing_markdown_prefix(package.manifest)
    if not package.markdown.startswith(prefix):
        raise ArtifactContractError("imported-writing Markdown is missing its generated-output boundary")
    source_content = package.markdown[len(prefix) :]
    if not 1 <= len(source_content) <= 1_048_576:
        raise ArtifactContractError("imported-writing source content is outside its supported bounds")
    if hashlib.sha256(source_content.encode("utf-8")).hexdigest() != package.manifest["content_sha256"]:
        raise ArtifactContractError("imported-writing source content digest mismatch")
    for label, name in (("output", _WRITING_OUTPUT_FILENAME), ("validation", _VALIDATION_FILENAME)):
        if package.manifest["files"].get(label) != _file_digest(name, package.files[name]):
            raise ArtifactContractError(f"imported-writing package {name} digest mismatch")


def _validate_loaded_imported_writing_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest["schema_version"] != _SCHEMA_VERSION or manifest["artifact_type"] != "imported_writing":
        raise ArtifactContractError("unsupported imported-writing manifest envelope")
    writing_id = _bounded_string(manifest["writing_id"], "writing_id", 1, 24)
    handoff_id = _bounded_string(manifest["handoff_id"], "imported-writing handoff_id", 1, 24)
    if not _WRITING_ID_RE.fullmatch(writing_id) or not _HANDOFF_ID_RE.fullmatch(handoff_id):
        raise ArtifactContractError("invalid imported-writing identity")
    incoming_digest = _required_digest(manifest["incoming_package_digest"], "incoming package digest")
    expected_identity = canonical_sha256({"handoff_id": handoff_id, "incoming_package_digest": incoming_digest})
    if writing_id != f"writing-{expected_identity[:16]}":
        raise ArtifactContractError("writing_id does not match its semantic identity")
    _required_digest(manifest["handoff_digest"], "imported-writing handoff digest")
    _required_digest(manifest["revision_content_digest"], "imported-writing revision digest")
    _required_digest(manifest["content_sha256"], "imported-writing content digest")
    dossier_key = _bounded_string(manifest["dossier_key"], "imported-writing dossier_key", 1, 100)
    revision_id = _bounded_string(manifest["revision_id"], "imported-writing revision_id", 1, 32)
    if not _DOSSIER_KEY_RE.fullmatch(dossier_key) or not _REVISION_ID_RE.fullmatch(revision_id):
        raise ArtifactContractError("invalid imported-writing dossier identity")
    if manifest["output_kind"] not in {"draft", "summary"}:
        raise ArtifactContractError("unsupported imported-writing output kind")
    includes_drafts = manifest["includes_drafts"]
    if not isinstance(includes_drafts, bool):
        raise ArtifactContractError("imported-writing includes_drafts must be a boolean")
    expected_visibility = "published_and_drafts" if includes_drafts else "published_only"
    if manifest["visibility"] != expected_visibility:
        raise ArtifactContractError("imported-writing visibility does not match its draft scope")
    if manifest["egress_acknowledged"] is not True:
        raise ArtifactContractError("imported-writing egress acknowledgement must be inherited")
    if manifest["draft_evidence_acknowledged"] is not includes_drafts:
        raise ArtifactContractError("imported-writing draft acknowledgement does not match its scope")
    _timestamp(manifest["source_created_at"], "imported-writing source timestamp")
    _timestamp(manifest["imported_at"], "imported-writing import timestamp")
    projected_agent = _project_writing_agent(manifest["agent"])
    if projected_agent != manifest["agent"]:
        raise ArtifactContractError("imported-writing agent metadata does not match its strict projection")
    _bounded_string(manifest["title"], "imported-writing title", 1, 500)
    if manifest["human_reviewed"] is not False:
        raise ArtifactContractError("automatic import cannot claim human review")
    warnings = _bounded_strings(manifest["warnings"], "imported-writing warnings", 100, 2000)
    if warnings != manifest["warnings"] or warnings != _deduplicated_strings(warnings):
        raise ArtifactContractError("imported-writing warnings must be a stable deduplicated array")
    summary = _allowlisted_mapping(
        manifest["validation"],
        _IMPORTED_WRITING_SUMMARY_FIELDS,
        "imported-writing validation summary",
        require_all=True,
    )
    for claim in ("schema_valid", "package_integrity", "dossier_current", "citations_resolved", "coverage_complete"):
        if summary[claim] is not True:
            raise ArtifactContractError("imported-writing manifest requires successful automatic claims")
    _bounded_integer(summary["unsupported_sections"], "unsupported section count", 0, 200)
    files = _allowlisted_mapping(
        manifest["files"],
        frozenset({"output", "validation"}),
        "imported-writing files",
        require_all=True,
    )
    for label, expected_path in (("output", _WRITING_OUTPUT_FILENAME), ("validation", _VALIDATION_FILENAME)):
        digest = _allowlisted_mapping(
            files[label],
            _FILE_DIGEST_FIELDS,
            f"imported-writing {label} file digest",
            require_all=True,
        )
        if digest["path"] != expected_path:
            raise ArtifactContractError(f"imported-writing {label} file path is inconsistent")
        _required_digest(digest["sha256"], f"imported-writing {label} file digest")
        _bounded_integer(digest["bytes"], f"imported-writing {label} file bytes", 0, _MAX_DOSSIER_MEMBER_BYTES)


def _validate_loaded_imported_writing_validation(
    manifest: Mapping[str, Any],
    validation: Mapping[str, Any],
) -> None:
    if validation["schema_version"] != _SCHEMA_VERSION or validation["artifact_type"] != "validation_result":
        raise ArtifactContractError("unsupported imported-writing validation envelope")
    if (
        validation["target_type"] != "imported_writing"
        or validation["target_id"] != manifest["writing_id"]
        or validation["target_digest"] != manifest["incoming_package_digest"]
    ):
        raise ArtifactContractError("imported-writing validation targets another artifact")
    expected_status = "valid_with_warnings" if manifest["warnings"] else "valid"
    if validation["status"] != expected_status:
        raise ArtifactContractError("imported-writing validation status does not match its warnings")
    claims = ("schema_valid", "package_integrity", "dossier_current", "citations_resolved", "coverage_complete")
    if any(validation[claim] is not True for claim in claims) or validation["human_reviewed"] is not False:
        raise ArtifactContractError("imported-writing automatic validation claims are inconsistent")
    citations = _bounded_sequence(validation["citations"], "imported-writing validation citations", 250)
    for item in citations:
        citation = _project_fields(item, _VALIDATION_CITATION_FIELDS, "imported-writing validation citation")
        if not isinstance(citation["citation_id"], str) or not _CITATION_ID_RE.fullmatch(citation["citation_id"]):
            raise ArtifactContractError("invalid imported-writing validation citation ID")
        if citation["status"] != "valid" or citation["reason"] is not None:
            raise ArtifactContractError("imported-writing citations must remain current")
    warnings = _bounded_strings(validation["warnings"], "imported-writing validation warnings", 100, 2000)
    errors = _bounded_strings(validation["errors"], "imported-writing validation errors", 100, 2000)
    if warnings != manifest["warnings"] or errors:
        raise ArtifactContractError("imported-writing validation diagnostics are inconsistent")
    if validation["validated_at"] != manifest["imported_at"]:
        raise ArtifactContractError("imported-writing validation timestamp does not match import time")
    _timestamp(validation["validated_at"], "imported-writing validation timestamp")
    summary = manifest["validation"]
    if any(summary[claim] is not validation[claim] for claim in claims):
        raise ArtifactContractError("imported-writing validation summary does not match validation.json")
    if summary["unsupported_sections"] > 0 and "unsupported_sections_present" not in manifest["warnings"]:
        raise ArtifactContractError("imported-writing unsupported sections require a stable warning")


def _imported_writing_semantic_projection(package: ImportedWritingPackage) -> dict[str, Any]:
    manifest = _copy_json(package.manifest)
    validation = _copy_json(package.validation)
    assert isinstance(manifest, dict) and isinstance(validation, dict)
    manifest.pop("imported_at")
    manifest.pop("files")
    validation.pop("validated_at")
    return {"manifest": manifest, "validation": validation, "markdown": package.markdown}


def _reuse_imported_writing_or_raise(
    target: Path,
    requested: ImportedWritingPackage,
) -> ImportedWritingPublication:
    try:
        stored = load_imported_writing_package(target)
    except UnsafeArtifactPathError:
        raise
    except ArtifactContractError as error:
        raise ArtifactCollisionError(f"immutable imported-writing collision: {target}") from error
    if _imported_writing_semantic_projection(stored) != _imported_writing_semantic_projection(requested):
        raise ArtifactCollisionError(f"immutable imported-writing collision: {target}")
    publish_directory_atomic(target, stored.files)
    return ImportedWritingPublication(status="reused", path=target, package=stored)


def _initial_dossier_validation(manifest: Mapping[str, Any], *, validated_at: str) -> dict[str, Any]:
    warnings = list(manifest["warnings"])
    return {
        "schema_version": _SCHEMA_VERSION,
        "artifact_type": "validation_result",
        "target_type": "dossier_revision",
        "target_id": manifest["revision_id"],
        "target_digest": manifest["content_digest"],
        "status": "valid_with_warnings" if warnings else "valid",
        "schema_valid": True,
        "package_integrity": True,
        "dossier_current": True,
        "citations_resolved": True,
        "coverage_complete": True,
        "human_reviewed": False,
        "citations": [
            {"citation_id": citation_id, "status": "valid", "reason": None} for citation_id in manifest["selected_citation_ids"]
        ],
        "warnings": warnings,
        "errors": [],
        "validated_at": validated_at,
    }


def _validate_initial_dossier_validation(
    manifest: Mapping[str, Any],
    validation: Mapping[str, Any],
) -> None:
    warnings = manifest.get("warnings")
    if not isinstance(warnings, list) or any(not isinstance(value, str) for value in warnings):
        raise ArtifactContractError("dossier warnings must be an array of strings")
    if manifest.get("status") not in {"ready", "degraded"}:
        raise ArtifactContractError("final dossier status must be ready or degraded")
    if manifest.get("status") == "degraded" and not warnings:
        raise ArtifactContractError("degraded dossier status requires an explanatory warning")
    includes_drafts = manifest.get("includes_drafts") is True
    if includes_drafts != (manifest.get("request", {}).get("visibility") == "published_and_drafts"):
        raise ArtifactContractError("dossier draft flag must mirror request visibility")
    if includes_drafts and "draft_visibility_enabled" not in warnings:
        raise ArtifactContractError("draft visibility warning is required for draft scope")

    expected_citations = [
        {"citation_id": citation_id, "status": "valid", "reason": None} for citation_id in manifest["selected_citation_ids"]
    ]
    expected_status = "valid_with_warnings" if warnings else "valid"
    claims = ("schema_valid", "package_integrity", "dossier_current", "citations_resolved", "coverage_complete")
    if (
        validation.get("schema_version") != _SCHEMA_VERSION
        or validation.get("artifact_type") != "validation_result"
        or validation.get("target_type") != "dossier_revision"
        or validation.get("target_id") != manifest.get("revision_id")
        or validation.get("target_digest") != manifest.get("content_digest")
        or validation.get("status") != expected_status
        or validation.get("warnings") != warnings
        or validation.get("errors") != []
        or validation.get("citations") != expected_citations
        or validation.get("human_reviewed") is not False
        or any(validation.get(claim) is not True for claim in claims)
    ):
        raise ArtifactContractError("initial dossier validation claims are inconsistent")
    _timestamp(validation.get("validated_at"), "initial validation timestamp")


def _validate_loaded_dossier_manifest(manifest: Mapping[str, Any]) -> None:
    request = _project_request(manifest["request"])
    if request != manifest["request"]:
        raise ArtifactContractError("dossier request does not match its strict projection")

    context = _project_corpus_context(manifest["corpus_context"])
    if context != manifest["corpus_context"]:
        raise ArtifactContractError("dossier corpus context does not match its strict projection")

    raw_candidates = _bounded_sequence(manifest["candidate_evidence"], "candidate evidence", 150)
    candidates = [_project_candidate(value) for value in raw_candidates]
    if candidates != raw_candidates:
        raise ArtifactContractError("dossier candidate evidence does not match its strict projection")

    raw_operations = _bounded_sequence(manifest["curation_operations"], "curation operations", 300)
    operations = [_project_curation_operation(value) for value in raw_operations]
    if operations != raw_operations:
        raise ArtifactContractError("dossier curation operations do not match their strict projection")

    derived_context = _project_derived_context(manifest["derived_context"])
    if derived_context != manifest["derived_context"]:
        raise ArtifactContractError("dossier derived context does not match its strict projection")

    selected_ids = _bounded_strings(manifest["selected_citation_ids"], "selected citation IDs", 100, 20)
    _validated_selected_citation_ids(candidates, selected_ids)

    warnings = _bounded_strings(manifest["warnings"], "dossier warnings", 100, 2000)
    if warnings != manifest["warnings"]:
        raise ArtifactContractError("dossier warnings do not match their strict projection")
    if not isinstance(manifest["includes_drafts"], bool):
        raise ArtifactContractError("dossier includes_drafts must be a boolean")

    parent_revision_id = manifest["parent_revision_id"]
    if parent_revision_id is not None and (
        not isinstance(parent_revision_id, str) or not _REVISION_ID_RE.fullmatch(parent_revision_id)
    ):
        raise ArtifactContractError("invalid parent dossier revision identifier")
    if not isinstance(manifest["content_digest"], str) or not _DIGEST_RE.fullmatch(manifest["content_digest"]):
        raise ArtifactContractError("invalid dossier content digest")
    if manifest["dossier_key"] != _dossier_key(request):
        raise ArtifactContractError("dossier key does not match the research request")

    _validate_loaded_file_digests(manifest["files"])


def _validate_loaded_file_digests(value: Any) -> None:
    files = _allowlisted_mapping(
        value,
        frozenset({"dossier", "validation"}),
        "dossier files",
        require_all=True,
    )
    for label, expected_path in (("dossier", _DOSSIER_FILENAME), ("validation", _VALIDATION_FILENAME)):
        digest = _allowlisted_mapping(
            files[label],
            _FILE_DIGEST_FIELDS,
            f"dossier {label} file digest",
            require_all=True,
        )
        if digest["path"] != expected_path:
            raise ArtifactContractError(f"dossier {label} file path is inconsistent")
        if not isinstance(digest["sha256"], str) or not _DIGEST_RE.fullmatch(digest["sha256"]):
            raise ArtifactContractError(f"dossier {label} file digest is invalid")
        _bounded_integer(digest["bytes"], f"dossier {label} file bytes", 0, _MAX_DOSSIER_MEMBER_BYTES)


def _validate_loaded_initial_validation(validation: Mapping[str, Any]) -> None:
    if validation["target_type"] != "dossier_revision":
        raise ArtifactContractError("initial validation must target a dossier revision")
    _bounded_string(validation["target_id"], "validation target_id", 1, 500)
    if not isinstance(validation["target_digest"], str) or not _DIGEST_RE.fullmatch(validation["target_digest"]):
        raise ArtifactContractError("validation target digest is invalid")
    if not isinstance(validation["status"], str) or validation["status"] not in {
        "valid",
        "valid_with_warnings",
        "invalid",
    }:
        raise ArtifactContractError("validation status is unsupported")

    boolean_fields = (
        "schema_valid",
        "package_integrity",
        "dossier_current",
        "citations_resolved",
        "coverage_complete",
        "human_reviewed",
    )
    if any(not isinstance(validation[field], bool) for field in boolean_fields):
        raise ArtifactContractError("validation claims must be booleans")

    citations = _bounded_sequence(validation["citations"], "validation citations", 250)
    for value in citations:
        citation = _project_fields(value, _VALIDATION_CITATION_FIELDS, "validation citation")
        if not isinstance(citation["citation_id"], str) or not _CITATION_ID_RE.fullmatch(citation["citation_id"]):
            raise ArtifactContractError("validation citation ID is invalid")
        if not isinstance(citation["status"], str) or citation["status"] not in {
            "valid",
            "missing",
            "changed",
            "hidden",
        }:
            raise ArtifactContractError("validation citation status is unsupported")
        reason = citation["reason"]
        if reason is not None and (not isinstance(reason, str) or len(reason) > 2000):
            raise ArtifactContractError("validation citation reason must contain at most 2000 characters")

    _bounded_strings(validation["warnings"], "validation warnings", 100, 2000)
    _bounded_strings(validation["errors"], "validation errors", 100, 2000)
    _timestamp(validation["validated_at"], "validation timestamp")


def _validate_dossier_package(package: DossierPackage) -> None:
    _validate_dossier_package_structure(package)
    try:
        serialized_manifest = json.loads(package.files[_MANIFEST_FILENAME])
        serialized_validation = json.loads(package.files[_VALIDATION_FILENAME])
        serialized_markdown = package.files[_DOSSIER_FILENAME].decode("utf-8")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArtifactContractError("dossier package files must be valid UTF-8 JSON/Markdown") from error
    if serialized_manifest != package.manifest or serialized_validation != package.validation:
        raise ArtifactContractError("dossier package object and serialized JSON disagree")
    if serialized_markdown != package.markdown:
        raise ArtifactContractError("dossier package object and serialized Markdown disagree")
    expected_digest = canonical_sha256(_dossier_content_projection(package.manifest))
    if package.manifest.get("content_digest") != expected_digest:
        raise ArtifactContractError("dossier package content digest mismatch")
    _validated_selected_citation_ids(
        package.manifest["candidate_evidence"],
        package.manifest.get("selected_citation_ids"),
    )
    if package.markdown != _render_dossier_markdown(package.manifest):
        raise ArtifactContractError("dossier Markdown does not match selected evidence")
    for label, name in (("dossier", _DOSSIER_FILENAME), ("validation", _VALIDATION_FILENAME)):
        if package.manifest["files"].get(label) != _file_digest(name, package.files[name]):
            raise ArtifactContractError(f"dossier package {name} digest mismatch")
    if package.validation.get("target_id") != package.manifest["revision_id"]:
        raise ArtifactContractError("dossier validation targets another revision")
    if package.validation.get("target_digest") != package.manifest["content_digest"]:
        raise ArtifactContractError("dossier validation targets another content digest")


def _validate_dossier_package_structure(package: DossierPackage) -> None:
    if not isinstance(package, DossierPackage):
        raise TypeError("package must be a DossierPackage")
    if set(package.files) != _DOSSIER_PACKAGE_FILENAMES:
        raise ArtifactContractError("dossier package must contain exactly three files")
    if set(package.manifest) != _MANIFEST_FIELDS or set(package.validation) != _VALIDATION_FIELDS:
        raise ArtifactContractError("dossier package fields do not match the contract allowlist")
    _validate_initial_dossier_validation(package.manifest, package.validation)
    if not _DOSSIER_KEY_RE.fullmatch(str(package.manifest.get("dossier_key", ""))):
        raise ArtifactContractError("invalid dossier package key")
    if not _REVISION_ID_RE.fullmatch(str(package.manifest.get("revision_id", ""))):
        raise ArtifactContractError("invalid dossier revision identifier")


def _project_fields(value: Any, fields: Sequence[str], label: str) -> dict[str, Any]:
    expected = set(fields)
    if isinstance(value, Mapping):
        if set(value) != expected:
            raise ArtifactContractError(f"{label} fields do not match the allowlist")
        return {field: value[field] for field in fields}
    try:
        return {field: getattr(value, field) for field in fields}
    except AttributeError as error:
        raise ArtifactContractError(f"{label} does not expose the required fields") from error


def _allowlisted_mapping(
    value: Any,
    allowed: frozenset[str],
    label: str,
    *,
    require_all: bool = False,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ArtifactContractError(f"{label} must be an object")
    fields = set(value)
    if not fields <= allowed or (require_all and fields != allowed):
        raise ArtifactContractError(f"{label} fields do not match the allowlist")
    return {key: _copy_json(item) for key, item in value.items()}


def _copy_json(value: Any) -> Any:
    if isinstance(value, Enum):
        return _copy_json(value.value)
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ArtifactContractError("artifact JSON cannot contain non-finite numbers")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ArtifactContractError("artifact JSON object keys must be strings")
        return {key: _copy_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_copy_json(item) for item in value]
    raise ArtifactContractError(f"unsupported artifact JSON value: {type(value).__name__}")


def _bounded_sequence(value: Any, label: str, maximum: int) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray) or len(value) > maximum:
        raise ArtifactContractError(f"{label} must be an array with at most {maximum} items")
    return list(value)


def _bounded_strings(value: Any, label: str, maximum_items: int, maximum_length: int) -> list[str]:
    values = _bounded_sequence(value, label, maximum_items)
    if any(not isinstance(item, str) or len(item) > maximum_length for item in values):
        raise ArtifactContractError(f"{label} must contain bounded strings")
    return values


def _deduplicated_strings(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _bounded_integer(value: Any, label: str, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ArtifactContractError(f"{label} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        raise ArtifactContractError(f"{label} is outside its supported bounds")
    return value


def _bounded_string(value: Any, label: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise ArtifactContractError(f"{label} must contain {minimum}..{maximum} characters")
    return value


def _optional_string(value: Any, label: str) -> str | None:
    if value is not None and (not isinstance(value, str) or not value):
        raise ArtifactContractError(f"{label} must be a non-empty string or null")
    return value


def _optional_bounded_string(value: Any, label: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _bounded_string(value, label, 1, maximum)


def _calendar_date(value: Any, label: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise ArtifactContractError(f"{label} must be YYYY-MM-DD or null")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ArtifactContractError(f"{label} must be a valid calendar date") from error


def _optional_timestamp(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _timestamp(value, label)


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _UTC_TIMESTAMP_RE.fullmatch(value):
        raise ArtifactContractError(f"{label} must be an RFC 3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + _UTC_OFFSET)
    except ValueError as error:
        raise ArtifactContractError(f"{label} must be an RFC 3339 UTC timestamp") from error
    offset = parsed.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ArtifactContractError(f"{label} must be UTC")
    return value


def _clock_values(clock: Callable[[], str | datetime]) -> tuple[str, str]:
    value = clock()
    if isinstance(value, datetime):
        offset = value.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ArtifactContractError("dossier clock must return a UTC time")
        parsed = value.astimezone(UTC)
    elif isinstance(value, str):
        _timestamp(value, "dossier clock")
        parsed = datetime.fromisoformat(value[:-1] + _UTC_OFFSET).astimezone(UTC)
    else:
        raise ArtifactContractError("dossier clock must return a UTC timestamp")
    validated_at = parsed.isoformat(timespec="seconds").replace(_UTC_OFFSET, "Z")
    return validated_at, parsed.strftime("%Y%m%dT%H%M%SZ")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _random_entropy() -> str:
    return secrets.token_hex(4)


def _file_digest(path: str, payload: bytes) -> dict[str, Any]:
    return {"path": path, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}


def _json_file_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _safe_markdown_inline(value: Any) -> str:
    if not isinstance(value, str):
        raise ArtifactContractError("Markdown fields must be strings")
    return _escape_markdown(_escape_controls(value, keep_newlines=False))


def _safe_markdown_quote(value: Any) -> list[str]:
    if not isinstance(value, str):
        raise ArtifactContractError("citation excerpt must be a string")
    escaped = _escape_markdown(_escape_controls(value, keep_newlines=True))
    return [f"> {line}" for line in escaped.split("\n")]


def _escape_controls(value: str, *, keep_newlines: bool) -> str:
    output: list[str] = []
    for character in value:
        codepoint = ord(character)
        if character == "\n" and keep_newlines:
            output.append(character)
        elif codepoint < 0x20 or 0x7F <= codepoint < 0xA0:
            output.append(f"\\u{codepoint:04x}")
        else:
            output.append(character)
    return "".join(output)


def _escape_markdown(value: str) -> str:
    escaped = html.escape(value, quote=True)
    for character in "\\`*_{}[]()#+-.!|>":
        escaped = escaped.replace(character, f"\\{character}")
    return escaped


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ArtifactContractError(f"duplicate JSON field: {key}")
        value[key] = item
    return value


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _read_imported_writing_directory(package_dir: Path) -> dict[str, bytes]:
    package_path = _absolute_path(package_dir)
    assert_no_symlink_components(package_path)
    try:
        mode = os.lstat(package_path).st_mode
    except FileNotFoundError as error:
        raise ArtifactContractError(f"imported-writing directory does not exist: {package_path}") from error
    if stat.S_ISLNK(mode):
        raise UnsafeArtifactPathError(f"symlink imported-writing directory is forbidden: {package_path}")
    if not stat.S_ISDIR(mode):
        raise ArtifactContractError(f"imported-writing path is not a real directory: {package_path}")

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(package_path, flags)
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.EMLINK}:
            raise UnsafeArtifactPathError(f"symlink imported-writing directory is forbidden: {package_path}") from error
        raise ArtifactContractError(f"cannot open imported-writing directory: {package_path}") from error
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ArtifactContractError(f"imported-writing path is not a real directory: {package_path}")
        names = set(os.listdir(descriptor))
        if names != _IMPORTED_WRITING_FILENAMES:
            raise ArtifactContractError("imported-writing directory must contain exactly three known files")
        files = {name: _read_dossier_member(descriptor, package_path, name) for name in sorted(names)}
        if set(os.listdir(descriptor)) != _IMPORTED_WRITING_FILENAMES:
            raise ArtifactContractError("imported-writing directory changed while being read")
        return files
    finally:
        os.close(descriptor)


def _read_dossier_directory(revision_dir: Path) -> dict[str, bytes]:
    revision = _absolute_path(revision_dir)
    assert_no_symlink_components(revision)
    try:
        mode = os.lstat(revision).st_mode
    except FileNotFoundError as error:
        raise ArtifactContractError(f"dossier revision directory does not exist: {revision}") from error
    if stat.S_ISLNK(mode):
        raise UnsafeArtifactPathError(f"symlink dossier revision is forbidden: {revision}")
    if not stat.S_ISDIR(mode):
        raise ArtifactContractError(f"dossier revision path is not a real directory: {revision}")

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(revision, flags)
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.EMLINK}:
            raise UnsafeArtifactPathError(f"symlink dossier revision is forbidden: {revision}") from error
        raise ArtifactContractError(f"cannot open dossier revision directory: {revision}") from error
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ArtifactContractError(f"dossier revision path is not a real directory: {revision}")
        names = set(os.listdir(descriptor))
        if names != _DOSSIER_PACKAGE_FILENAMES:
            raise ArtifactContractError("dossier revision directory must contain exactly three known files")
        files = {name: _read_dossier_member(descriptor, revision, name) for name in sorted(names)}
        if set(os.listdir(descriptor)) != _DOSSIER_PACKAGE_FILENAMES:
            raise ArtifactContractError("dossier revision directory changed while being read")
        return files
    finally:
        os.close(descriptor)


def _read_dossier_member(directory_descriptor: int, revision: Path, name: str) -> bytes:
    try:
        path_stat = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError as error:
        raise ArtifactContractError(f"dossier package member is missing: {name}") from error
    if stat.S_ISLNK(path_stat.st_mode):
        raise UnsafeArtifactPathError(f"symlink dossier package member is forbidden: {revision / name}")
    if not stat.S_ISREG(path_stat.st_mode):
        raise ArtifactContractError(f"dossier package member is not a regular file: {name}")
    _validate_dossier_member_size(name, path_stat.st_size)

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.EMLINK}:
            raise UnsafeArtifactPathError(f"symlink dossier package member is forbidden: {revision / name}") from error
        raise ArtifactContractError(f"cannot open dossier package member: {name}") from error
    try:
        opened_stat = os.fstat(descriptor)
        if stat.S_ISLNK(opened_stat.st_mode):
            raise UnsafeArtifactPathError(f"symlink dossier package member is forbidden: {revision / name}")
        if not stat.S_ISREG(opened_stat.st_mode):
            raise ArtifactContractError(f"dossier package member is not a regular file: {name}")
        if (opened_stat.st_dev, opened_stat.st_ino) != (path_stat.st_dev, path_stat.st_ino):
            raise UnsafeArtifactPathError(f"dossier package member changed during secure open: {revision / name}")
        _validate_dossier_member_size(name, opened_stat.st_size)
        payload = _read_bounded_descriptor(descriptor, name)
        final_stat = os.fstat(descriptor)
        if final_stat.st_size != len(payload) or final_stat.st_mtime_ns != opened_stat.st_mtime_ns:
            raise ArtifactContractError(f"dossier package member changed while being read: {name}")
        return payload
    finally:
        os.close(descriptor)


def _validate_dossier_member_size(name: str, size: int) -> None:
    if size > _MAX_DOSSIER_MEMBER_BYTES:
        raise ArtifactContractError(f"dossier package member {name} exceeds {_MAX_DOSSIER_MEMBER_BYTES} byte limit")


def _read_bounded_descriptor(descriptor: int, name: str) -> bytes:
    payload = bytearray()
    while True:
        remaining = _MAX_DOSSIER_MEMBER_BYTES + 1 - len(payload)
        if remaining <= 0:
            raise ArtifactContractError(f"dossier package member {name} exceeds {_MAX_DOSSIER_MEMBER_BYTES} byte limit")
        chunk = os.read(descriptor, min(1024 * 1024, remaining))
        if not chunk:
            return bytes(payload)
        payload.extend(chunk)


def _ensure_owner_directory(path: Path) -> None:
    absolute = _absolute_path(path)
    assert_no_symlink_components(absolute)
    missing: list[Path] = []
    current = absolute
    while not os.path.lexists(current):
        missing.append(current)
        parent = current.parent
        if parent == current:
            raise UnsafeArtifactPathError(f"cannot establish artifact directory: {absolute}")
        current = parent
    mode = os.lstat(current).st_mode
    if not stat.S_ISDIR(mode):
        raise UnsafeArtifactPathError(f"artifact parent is not a directory: {current}")

    for directory in reversed(missing):
        try:
            os.mkdir(directory, 0o700)
        except FileExistsError:
            assert_no_symlink_components(directory)
        mode = os.lstat(directory).st_mode
        if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
            raise UnsafeArtifactPathError(f"artifact path is not a real directory: {directory}")
        os.chmod(directory, 0o700)

    assert_no_symlink_components(absolute)
    mode = os.lstat(absolute).st_mode
    if not stat.S_ISDIR(mode):
        raise UnsafeArtifactPathError(f"artifact parent is not a directory: {absolute}")
    if stat.S_IMODE(mode) != 0o700:
        raise UnsafeArtifactPathError(f"artifact directory must have mode 0700: {absolute}")


def _validated_package_files(files: Mapping[str, bytes]) -> dict[str, bytes]:
    validated: dict[str, bytes] = {}
    for name, payload in files.items():
        if not isinstance(name, str) or not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
            raise UnsafeArtifactPathError(f"package file must be one safe relative name: {name!r}")
        if not isinstance(payload, bytes):
            raise TypeError(f"package payload for {name!r} must be bytes")
        validated[name] = payload
    return validated


def _write_owner_file(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _reuse_file_or_raise(path: Path, payload: bytes) -> PublishStatus:
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError as error:
        raise ArtifactCollisionError(f"artifact disappeared during collision check: {path}") from error
    if stat.S_ISLNK(mode):
        raise UnsafeArtifactPathError(f"symlink artifact target is forbidden: {path}")
    if not stat.S_ISREG(mode) or path.read_bytes() != payload:
        raise ArtifactCollisionError(f"immutable artifact collision: {path}")
    os.chmod(path, 0o600)
    return "reused"


def _reuse_directory_or_raise(path: Path, files: Mapping[str, bytes]) -> PublishStatus:
    mode = os.lstat(path).st_mode
    if stat.S_ISLNK(mode):
        raise UnsafeArtifactPathError(f"symlink artifact target is forbidden: {path}")
    if not stat.S_ISDIR(mode):
        raise ArtifactCollisionError(f"immutable artifact collision: {path}")
    entries = {entry.name: entry for entry in os.scandir(path)}
    if set(entries) != set(files):
        raise ArtifactCollisionError(f"immutable artifact collision: {path}")
    for name, payload in files.items():
        entry = entries[name]
        if entry.is_symlink() or not entry.is_file(follow_symlinks=False) or Path(entry.path).read_bytes() != payload:
            raise ArtifactCollisionError(f"immutable artifact collision: {path / name}")
    os.chmod(path, 0o700)
    for name in files:
        os.chmod(path / name, 0o600)
    return "reused"


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
