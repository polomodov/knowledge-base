"""Research dossier and writing-handoff CLI handlers."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from knowledge_base.arango import ArangoClient
from knowledge_base.cli import common as cli_common
from knowledge_base.cli.common import CliUsageError
from knowledge_base.embeddings import build_embedding_provider
from knowledge_base.json_output import emit_json
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.research_artifacts import (
    ArtifactContractError,
    assert_no_symlink_components,
    load_dossier_package,
    load_imported_writing_package,
    materialize_curated_dossier_package,
    materialize_dossier_package,
    materialize_imported_writing_package,
    publish_dossier_package,
    publish_imported_writing_package,
    validate_output_root,
)
from knowledge_base.research_workflow import (
    CurationOperation,
    DossierCurationError,
    DossierRevision,
    ResearchRequest,
    ResearchVisibility,
    build_dossier,
    curate_dossier_revision,
    validate_dossier_revision,
)
from knowledge_base.writing_handoff import (
    RequestedWritingOutput,
    WritingHandoffError,
    WritingImportError,
    build_writing_handoff,
    load_writing_handoff,
    load_writing_output_package,
    prepare_writing_import,
    publish_writing_handoff,
    validate_imported_writing_package,
    validate_writing_handoff,
    validate_writing_output_package,
)

_SAFE_CURATION_REJECTION_CODES = frozenset(
    {
        "parent_not_current",
        "include_not_current",
        "unknown_citation",
        "invalid_transition",
        "invalid_operation",
        "empty_operations",
        "invalid_operation_order",
        "duplicate_operation",
        "conflicting_operation",
        "empty_selection",
        "selection_limit_exceeded",
        "validation_unavailable",
        "invalid_parent",
    }
)
_SAFE_HANDOFF_REJECTION_CODES = frozenset(
    {
        "external_disclosure_not_acknowledged",
        "draft_evidence_not_acknowledged",
        "dossier_not_current",
    }
)
_SAFE_IMPORT_REJECTION_CODES = frozenset({"writing_output_invalid"})
_RESEARCH_ARTIFACT_TYPES = frozenset(
    {
        "dossier_revision",
        "writing_handoff",
        "writing_output",
        "imported_writing",
    }
)
_RESEARCH_ARTIFACT_PROBE_MAX_BYTES = 32 * 1024 * 1024
_RESEARCH_STANDALONE_PROBE_MAX_BYTES = 2 * 1024 * 1024
_DOSSIER_KEY_RE = re.compile(r"^research-[a-z0-9_-]+-[0-9a-f]{12}$")
_REVISION_ID_RE = re.compile(r"(?a:^rev-\d{8}T\d{6}Z-[0-9a-f]{8}$)")
_HANDOFF_ID_RE = re.compile(r"^handoff-[0-9a-f]{16}$")


class _CurationOperationAction(argparse.Action):
    """Collect mixed repeated curation flags in their exact command-line order."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        del parser
        if option_string is None or not isinstance(values, str):
            raise CliUsageError("curation operations require one citation identifier")
        operation = option_string.removeprefix("--")
        current = getattr(namespace, self.dest, None)
        ordered = [] if current is None else list(current)
        ordered.append((operation, values))
        setattr(namespace, self.dest, ordered)


def _research_build(args: argparse.Namespace) -> int:
    request = ResearchRequest(
        query=args.topic,
        source_key=args.source,
        published_from=args.published_from,
        published_to=args.published_to,
        visibility=(ResearchVisibility.PUBLISHED_AND_DRAFTS if args.include_drafts else ResearchVisibility.PUBLISHED_ONLY),
        document_limit=args.documents,
        fragments_per_document=args.fragments_per_document,
    )
    settings = cli_common._settings(args)
    generated_root = Path(settings.repo_root) / "data" / "generated"
    output_root = Path(args.output_root).expanduser() if args.output_root is not None else generated_root / "research"
    location_warning = validate_output_root(
        output_root,
        generated_root=generated_root,
        acknowledge_unsafe=args.acknowledge_unsafe_output,
    )
    args._error_warnings = cli_common._research_warning_codes((location_warning,))

    repository = KnowledgeRepository(ArangoClient(settings))
    provider = build_embedding_provider(settings)
    result = build_dossier(
        repository,
        request,
        provider=provider,
        built_at=_utc_timestamp(),
    )
    args._error_warnings = cli_common._research_warning_codes(result.warnings, (location_warning,))
    if not result.publishable:
        warnings = cli_common._research_warning_codes(result.warnings, (location_warning,))
        cli_common._emit_research_warnings(warnings)
        return emit_json(
            {
                "status": "no_evidence",
                "dossier_key": None,
                "revision_id": None,
                "content_digest": None,
                "output": None,
                "evidence": len(result.selected_citation_ids),
                "candidates": len(result.candidate_evidence),
                "includes_drafts": result.includes_drafts,
                "warnings": list(warnings),
            },
            exit_code=1,
        )

    artifact_status: Literal["ready", "degraded"] = "ready" if result.status == "ready" else "degraded"
    package = materialize_dossier_package(
        request=result.request,
        corpus_context=result.corpus_context,
        candidate_evidence=result.candidate_evidence,
        derived_context=result.derived_context,
        warnings=result.warnings,
        status=artifact_status,
    )
    publish_dossier_package(output_root, package)
    manifest = package.manifest
    warnings = cli_common._research_warning_codes(manifest["warnings"], (location_warning,))
    cli_common._emit_research_warnings(warnings)
    revision_path = output_root / manifest["dossier_key"] / "revisions" / manifest["revision_id"]
    status = {"ready": "ok", "degraded": "degraded"}[manifest["status"]]
    return emit_json(
        {
            "status": status,
            "dossier_key": manifest["dossier_key"],
            "revision_id": manifest["revision_id"],
            "content_digest": manifest["content_digest"],
            "output": str(revision_path),
            "evidence": len(manifest["selected_citation_ids"]),
            "candidates": len(manifest["candidate_evidence"]),
            "includes_drafts": manifest["includes_drafts"],
            "warnings": list(warnings),
        },
    )


def _research_validate(args: argparse.Namespace) -> int:
    artifact = Path(args.artifact).expanduser()
    artifact_type = _research_artifact_type(artifact)

    if artifact_type == "dossier_revision":
        package = load_dossier_package(artifact)
        revision = DossierRevision(**package.manifest)
        settings = cli_common._settings(args)
        repository = KnowledgeRepository(ArangoClient(settings))
        result = validate_dossier_revision(
            repository,
            revision,
            validated_at=_utc_timestamp(),
        )
        return _emit_research_validation(args, result)

    settings = cli_common._settings(args)
    _, output_root = _research_output_roots(settings, args.output_root)

    if artifact_type == "writing_handoff":
        handoff = load_writing_handoff(artifact)
        revision_package = load_dossier_package(
            _linked_dossier_revision_path(output_root, handoff.dossier_key, handoff.revision_id)
        )
        revision = DossierRevision(**revision_package.manifest)
        repository = KnowledgeRepository(ArangoClient(settings))
        result = validate_writing_handoff(
            repository,
            revision,
            handoff,
            validated_at=_utc_timestamp(),
        )
    elif artifact_type == "writing_output":
        if args.handoff is None:
            raise CliUsageError("--handoff is required when validating an incoming writing_output artifact")
        output = load_writing_output_package(artifact)
        handoff = load_writing_handoff(Path(args.handoff).expanduser())
        revision_package = load_dossier_package(
            _linked_dossier_revision_path(output_root, handoff.dossier_key, handoff.revision_id)
        )
        revision = DossierRevision(**revision_package.manifest)
        repository = KnowledgeRepository(ArangoClient(settings))
        result = validate_writing_output_package(
            repository,
            revision,
            handoff,
            output,
            validated_at=_utc_timestamp(),
        )
    elif artifact_type == "imported_writing":
        imported = load_imported_writing_package(artifact)
        manifest = imported.manifest
        handoff = load_writing_handoff(_linked_handoff_path(output_root, manifest["dossier_key"], manifest["handoff_id"]))
        revision_package = load_dossier_package(
            _linked_dossier_revision_path(output_root, handoff.dossier_key, handoff.revision_id)
        )
        revision = DossierRevision(**revision_package.manifest)
        repository = KnowledgeRepository(ArangoClient(settings))
        result = validate_imported_writing_package(
            repository,
            revision,
            handoff,
            imported,
            validated_at=_utc_timestamp(),
        )
    else:  # pragma: no cover - _research_artifact_type is exhaustive
        raise ArtifactContractError("unsupported research artifact type")

    return _emit_research_validation(args, result)


def _emit_research_validation(args: argparse.Namespace, result) -> int:
    warnings = cli_common._research_warning_codes(result.warnings)
    args._error_warnings = warnings
    cli_common._emit_research_warnings(warnings)
    return emit_json(asdict(result), exit_code=0 if result.status in {"valid", "valid_with_warnings"} else 1)


def _research_curate(args: argparse.Namespace) -> int:
    raw_operations = args.curation_operations
    if not raw_operations:
        raise CliUsageError("at least one include, exclude or pin operation is required")
    operations = tuple(
        CurationOperation(
            operation=operation,
            citation_id=citation_id,
            reason=args.reason,
            ordinal=ordinal,
        )
        for ordinal, (operation, citation_id) in enumerate(raw_operations)
    )

    settings = cli_common._settings(args)
    generated_root = Path(settings.repo_root) / "data" / "generated"
    output_root = Path(args.output_root).expanduser() if args.output_root is not None else generated_root / "research"
    location_warning = validate_output_root(
        output_root,
        generated_root=generated_root,
        acknowledge_unsafe=args.acknowledge_unsafe_output,
    )
    args._error_warnings = cli_common._research_warning_codes((location_warning,))

    parent_package = load_dossier_package(Path(args.revision).expanduser())
    parent_revision = DossierRevision(**parent_package.manifest)
    repository = KnowledgeRepository(ArangoClient(settings))
    try:
        result = curate_dossier_revision(
            repository,
            parent_revision,
            operations,
            validated_at=_utc_timestamp(),
        )
    except DossierCurationError as error:
        validation_warnings = error.parent_validation.warnings if error.parent_validation is not None else ()
        warnings = cli_common._research_warning_codes(validation_warnings, (location_warning,))
        args._error_warnings = warnings
        cli_common._emit_research_warnings(warnings)
        reason = error.code if error.code in _SAFE_CURATION_REJECTION_CODES else "curation_rejected"
        payload: dict[str, Any] = {
            "status": "rejected",
            "reason": reason,
            "warnings": list(warnings),
        }
        if error.parent_validation is not None:
            payload["validation"] = asdict(error.parent_validation)
        return emit_json(payload, exit_code=1)

    child_package = materialize_curated_dossier_package(parent_package, result)
    publish_dossier_package(output_root, child_package)
    manifest = child_package.manifest
    warnings = cli_common._research_warning_codes(manifest["warnings"], (location_warning,))
    args._error_warnings = warnings
    cli_common._emit_research_warnings(warnings)
    revision_path = output_root / manifest["dossier_key"] / "revisions" / manifest["revision_id"]
    return emit_json(
        {
            "status": "ok",
            "dossier_key": manifest["dossier_key"],
            "revision_id": manifest["revision_id"],
            "parent_revision_id": manifest["parent_revision_id"],
            "content_digest": manifest["content_digest"],
            "output": str(revision_path),
            "operations": len(manifest["curation_operations"]),
            "includes_drafts": manifest["includes_drafts"],
            "warnings": list(warnings),
        }
    )


def _research_handoff(args: argparse.Namespace) -> int:
    requested = RequestedWritingOutput(
        kind=args.output_kind,
        language=args.language,
        style=args.style,
        max_words=args.max_words,
    )
    settings = cli_common._settings(args)
    generated_root, output_root = _research_output_roots(settings, args.output_root)
    revision_package = load_dossier_package(Path(args.revision).expanduser())
    revision = DossierRevision(**revision_package.manifest)
    repository = KnowledgeRepository(ArangoClient(settings))

    try:
        package = build_writing_handoff(
            repository,
            revision,
            requested,
            egress_acknowledged=args.acknowledge_external_disclosure,
            allow_draft_evidence=args.allow_draft_evidence,
            validated_at=_utc_timestamp(),
            created_at=_utc_timestamp(),
        )
        preflight_warning = None
        if args.acknowledge_unsafe_output:
            preflight_warning = validate_output_root(
                output_root,
                generated_root=generated_root,
                acknowledge_unsafe=True,
            )
        args._error_warnings = cli_common._research_warning_codes(package.warnings, (preflight_warning,))
        publication = publish_writing_handoff(
            output_root,
            package,
            generated_root=generated_root,
            acknowledge_unsafe=args.acknowledge_unsafe_output,
        )
    except WritingHandoffError as error:
        validation = error.validation
        warnings = cli_common._research_warning_codes(validation.warnings if validation is not None else ())
        args._error_warnings = warnings
        cli_common._emit_research_warnings(warnings)
        reason = error.code if error.code in _SAFE_HANDOFF_REJECTION_CODES else "handoff_rejected"
        payload: dict[str, Any] = {
            "status": "rejected",
            "reason": reason,
            "warnings": list(warnings),
        }
        if validation is not None:
            payload["validation"] = asdict(validation)
        return emit_json(payload, exit_code=1)

    published = publication.package
    warnings = cli_common._research_warning_codes(published.warnings, (publication.location_warning,))
    args._error_warnings = warnings
    cli_common._emit_research_warnings(warnings)
    return emit_json(
        {
            "status": "ok",
            "handoff_id": published.handoff_id,
            "dossier_key": published.dossier_key,
            "revision_id": published.revision_id,
            "package_digest": published.package_digest,
            "output": str(publication.path),
            "output_kind": _artifact_field(published.requested_output, "kind"),
            "evidence": len(published.evidence),
            "includes_drafts": published.includes_drafts,
            "egress_acknowledged": published.egress_acknowledged,
            "draft_evidence_acknowledged": published.draft_evidence_acknowledged,
            "warnings": list(warnings),
        }
    )


def _research_import_output(args: argparse.Namespace) -> int:
    settings = cli_common._settings(args)
    generated_root, output_root = _research_output_roots(settings, args.output_root)
    location_warning = validate_output_root(
        output_root,
        generated_root=generated_root,
        acknowledge_unsafe=args.acknowledge_unsafe_output,
    )
    args._error_warnings = cli_common._research_warning_codes((location_warning,))

    output = load_writing_output_package(Path(args.package).expanduser())
    handoff = load_writing_handoff(Path(args.handoff).expanduser())
    revision_package = load_dossier_package(_linked_dossier_revision_path(output_root, handoff.dossier_key, handoff.revision_id))
    revision = DossierRevision(**revision_package.manifest)
    repository = KnowledgeRepository(ArangoClient(settings))

    try:
        result = prepare_writing_import(
            repository,
            revision,
            handoff,
            output,
            validated_at=_utc_timestamp(),
        )
    except WritingImportError as error:
        validation = error.validation
        validation_warnings = validation.warnings if validation is not None else ()
        warnings = cli_common._research_warning_codes(validation_warnings, (location_warning,))
        args._error_warnings = warnings
        cli_common._emit_research_warnings(warnings)
        reason = error.code if error.code in _SAFE_IMPORT_REJECTION_CODES else "writing_import_rejected"
        payload: dict[str, Any] = {
            "status": "rejected",
            "reason": reason,
            "warnings": list(warnings),
        }
        if validation is not None:
            payload["validation"] = asdict(validation)
        return emit_json(payload, exit_code=1)

    args._error_warnings = cli_common._research_warning_codes(result.validation.warnings, (location_warning,))
    package = materialize_imported_writing_package(
        result.output,
        result.handoff,
        result.validation,
        imported_at=_utc_timestamp(),
    )
    publication = publish_imported_writing_package(output_root, package)
    published = publication.package
    manifest = published.manifest
    validation_summary = manifest["validation"]
    warnings = cli_common._research_warning_codes(manifest["warnings"], (location_warning,))
    args._error_warnings = warnings
    cli_common._emit_research_warnings(warnings)
    return emit_json(
        {
            "status": "ok",
            "writing_id": manifest["writing_id"],
            "output_kind": manifest["output_kind"],
            "dossier_key": manifest["dossier_key"],
            "revision_id": manifest["revision_id"],
            "output": str(publication.path),
            "citations_resolved": validation_summary["citations_resolved"],
            "coverage_complete": validation_summary["coverage_complete"],
            "unsupported_sections": validation_summary["unsupported_sections"],
            "human_reviewed": manifest["human_reviewed"],
            "warnings": list(warnings),
        }
    )


def _research_output_roots(settings, output_root: str | None) -> tuple[Path, Path]:
    generated_root = Path(settings.repo_root) / "data" / "generated"
    research_root = Path(output_root).expanduser() if output_root is not None else generated_root / "research"
    return generated_root, research_root


def _linked_dossier_revision_path(output_root: Path, dossier_key: Any, revision_id: Any) -> Path:
    if not isinstance(dossier_key, str) or not _DOSSIER_KEY_RE.fullmatch(dossier_key):
        raise ArtifactContractError("linked artifact has an invalid dossier_key")
    if not isinstance(revision_id, str) or not _REVISION_ID_RE.fullmatch(revision_id):
        raise ArtifactContractError("linked artifact has an invalid revision_id")
    return output_root / dossier_key / "revisions" / revision_id


def _linked_handoff_path(output_root: Path, dossier_key: Any, handoff_id: Any) -> Path:
    if not isinstance(dossier_key, str) or not _DOSSIER_KEY_RE.fullmatch(dossier_key):
        raise ArtifactContractError("linked artifact has an invalid dossier_key")
    if not isinstance(handoff_id, str) or not _HANDOFF_ID_RE.fullmatch(handoff_id):
        raise ArtifactContractError("linked artifact has an invalid handoff_id")
    return output_root / dossier_key / "handoffs" / f"{handoff_id}.json"


def _artifact_field(value: Any, field: str) -> Any:
    if isinstance(value, Mapping):
        return value[field]
    return getattr(value, field)


def _research_artifact_type(path: Path) -> str:
    """Read only the bounded local marker needed to select a strict artifact loader."""

    try:
        target_stat = os.lstat(path)
    except FileNotFoundError:
        # Preserve the existing dossier loader boundary for a missing path. It will
        # produce the canonical package error without opening a DB connection.
        return "dossier_revision"
    assert_no_symlink_components(path)
    if stat.S_ISDIR(target_stat.st_mode):
        marker = path / "manifest.json"
        maximum = _RESEARCH_ARTIFACT_PROBE_MAX_BYTES
    elif stat.S_ISREG(target_stat.st_mode):
        marker = path
        maximum = _RESEARCH_STANDALONE_PROBE_MAX_BYTES
    else:
        raise ArtifactContractError("research artifact must be a regular file or directory package")

    payload = _read_research_artifact_marker(marker, maximum=maximum)
    try:
        decoded = payload.decode("utf-8")
        parsed = json.loads(decoded, object_pairs_hook=_artifact_probe_object)
    except ArtifactContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ArtifactContractError("research artifact marker must be strict UTF-8 JSON") from None
    if not isinstance(parsed, dict):
        raise ArtifactContractError("research artifact marker must be a JSON object")
    artifact_type = parsed.get("artifact_type")
    if not isinstance(artifact_type, str) or artifact_type not in _RESEARCH_ARTIFACT_TYPES:
        raise ArtifactContractError("unsupported research artifact type")
    return artifact_type


def _read_research_artifact_marker(path: Path, *, maximum: int) -> bytes:
    assert_no_symlink_components(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ArtifactContractError("research artifact marker is not readable") from error
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ArtifactContractError("research artifact marker must be a regular file")
        if file_stat.st_size > maximum:
            raise ArtifactContractError("research artifact marker exceeds the size limit")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > maximum:
            raise ArtifactContractError("research artifact marker exceeds the size limit")
        return payload
    finally:
        os.close(descriptor)


def _artifact_probe_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ArtifactContractError(f"duplicate JSON field in research artifact marker: {key}")
        value[key] = item
    return value


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
