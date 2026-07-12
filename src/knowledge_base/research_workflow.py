from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

JsonObject = dict[str, Any]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CITATION_ID_RE = re.compile(r"^cit-[0-9a-f]{16}$")
_DOSSIER_KEY_RE = re.compile(r"^research-[a-z0-9_-]+-[0-9a-f]{12}$")
_REVISION_ID_RE = re.compile(r"^rev-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class ResearchVisibility(StrEnum):
    PUBLISHED_ONLY = "published_only"
    PUBLISHED_AND_DRAFTS = "published_and_drafts"

    @property
    def document_statuses(self) -> tuple[str, ...]:
        if self is ResearchVisibility.PUBLISHED_ONLY:
            return ("published",)
        return ("published", "draft")

    @property
    def includes_drafts(self) -> bool:
        return self is ResearchVisibility.PUBLISHED_AND_DRAFTS


@dataclass(frozen=True, slots=True)
class ResearchRequest:
    query: str
    source_key: str | None = None
    published_from: str | None = None
    published_to: str | None = None
    visibility: ResearchVisibility | str = ResearchVisibility.PUBLISHED_ONLY
    document_limit: int = 12
    fragments_per_document: int = 2
    evidence_limit: int | None = None
    candidate_limit: int | None = None
    retrieval: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.query, str):
            raise ValueError("query must be a string")
        query = self.query.strip()
        if not 1 <= len(query) <= 1000:
            raise ValueError("query must contain 1..1000 Unicode code points after trim")
        object.__setattr__(self, "query", query)

        if self.source_key is not None and (not isinstance(self.source_key, str) or not self.source_key):
            raise ValueError("source_key must be a non-empty string or null")

        try:
            visibility = ResearchVisibility(self.visibility)
        except (TypeError, ValueError) as error:
            raise ValueError("unsupported research visibility") from error
        object.__setattr__(self, "visibility", visibility)

        _bounded_integer("document_limit", self.document_limit, minimum=1, maximum=50)
        _bounded_integer("fragments_per_document", self.fragments_per_document, minimum=1, maximum=5)

        evidence_limit = self.evidence_limit
        if evidence_limit is None:
            evidence_limit = min(100, self.document_limit * self.fragments_per_document)
        _bounded_integer("evidence_limit", evidence_limit, minimum=1, maximum=100)

        candidate_limit = self.candidate_limit
        if candidate_limit is None:
            candidate_limit = min(150, max(36, self.document_limit * 3, evidence_limit))
        _bounded_integer("candidate_limit", candidate_limit, minimum=1, maximum=150)
        if evidence_limit > candidate_limit:
            raise ValueError("evidence_limit must not exceed candidate_limit")
        object.__setattr__(self, "evidence_limit", evidence_limit)
        object.__setattr__(self, "candidate_limit", candidate_limit)

        start = _parse_calendar_date("published_from", self.published_from)
        end = _parse_calendar_date("published_to", self.published_to)
        if start is not None and end is not None and start > end:
            raise ValueError("published_from must not be later than published_to")
        if not isinstance(self.retrieval, Mapping):
            raise ValueError("retrieval must be an object")
        object.__setattr__(self, "retrieval", dict(self.retrieval))

    @property
    def document_statuses(self) -> tuple[str, ...]:
        return ResearchVisibility(self.visibility).document_statuses

    @property
    def includes_drafts(self) -> bool:
        return ResearchVisibility(self.visibility).includes_drafts

    @property
    def published_from_utc(self) -> str | None:
        value = _parse_calendar_date("published_from", self.published_from)
        return f"{value.isoformat()}T00:00:00Z" if value is not None else None

    @property
    def published_to_exclusive_utc(self) -> str | None:
        value = _parse_calendar_date("published_to", self.published_to)
        return f"{(value + timedelta(days=1)).isoformat()}T00:00:00Z" if value is not None else None


@dataclass(frozen=True, slots=True)
class Citation:
    citation_id: str
    identity_sha256: str
    projection_version: str
    source_key: str
    canonical_id: str
    document_key: str
    chunk_key: str
    chunk_ordinal: int
    char_start: int
    char_end: int
    offset_basis: str
    excerpt: str
    excerpt_sha256: str
    title: str
    published_at: str | None
    document_status: str
    url: str | None
    raw_snapshot_key: str | None
    import_run_key: str | None
    captured_at: str | None

    def __post_init__(self) -> None:
        _bounded_string("source_key", self.source_key, minimum=1, maximum=256)
        _bounded_string("canonical_id", self.canonical_id, minimum=1, maximum=1024)
        _bounded_string("document_key", self.document_key, minimum=1, maximum=256)
        _bounded_string("chunk_key", self.chunk_key, minimum=1, maximum=256)
        _bounded_integer("chunk_ordinal", self.chunk_ordinal, minimum=0)
        _bounded_integer("char_start", self.char_start, minimum=0)
        _bounded_integer("char_end", self.char_end, minimum=1)
        if self.char_start >= self.char_end:
            raise ValueError("citation offsets must satisfy start < end")
        if self.projection_version != "citation-v1":
            raise ValueError("unsupported citation projection_version")
        if self.offset_basis != "normalized_whitespace_v1":
            raise ValueError("unsupported citation offset_basis")
        _bounded_string("excerpt", self.excerpt, minimum=1, maximum=20_000)
        if self.char_end - self.char_start != len(self.excerpt):
            raise ValueError("citation offsets must cover the exact excerpt")
        _bounded_string("title", self.title, minimum=0, maximum=2000)
        if self.document_status not in {"published", "draft"}:
            raise ValueError("unsupported citation document_status")
        _validate_timestamp("published_at", self.published_at)
        _validate_timestamp("captured_at", self.captured_at)
        _optional_bounded_string("raw_snapshot_key", self.raw_snapshot_key, maximum=256)
        _optional_bounded_string("import_run_key", self.import_run_key, maximum=256)
        _validate_http_url(self.url)

        expected_excerpt_digest = hashlib.sha256(self.excerpt.encode("utf-8")).hexdigest()
        if not _SHA256_RE.fullmatch(self.excerpt_sha256) or self.excerpt_sha256 != expected_excerpt_digest:
            raise ValueError("excerpt_sha256 does not match excerpt")

        identity_projection = {
            "canonical_id": self.canonical_id,
            "char_end": self.char_end,
            "char_start": self.char_start,
            "chunk_key": self.chunk_key,
            "document_key": self.document_key,
            "excerpt_sha256": self.excerpt_sha256,
            "offset_basis": self.offset_basis,
            "projection_version": self.projection_version,
            "source_key": self.source_key,
        }
        expected_identity = _sha256_json(identity_projection)
        if not _SHA256_RE.fullmatch(self.identity_sha256) or self.identity_sha256 != expected_identity:
            raise ValueError("identity_sha256 does not match citation identity")
        if not _CITATION_ID_RE.fullmatch(self.citation_id) or self.citation_id != f"cit-{expected_identity[:16]}":
            raise ValueError("citation_id does not match citation identity")


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    citation: Citation | Mapping[str, Any]
    document_rank: int
    fragment_rank: int
    score: float
    score_components: Mapping[str, float | None]
    selection_state: str
    selection_reason: str

    def __post_init__(self) -> None:
        citation = self.citation if isinstance(self.citation, Citation) else Citation(**dict(self.citation))
        object.__setattr__(self, "citation", citation)
        _bounded_integer("document_rank", self.document_rank, minimum=1)
        _bounded_integer("fragment_rank", self.fragment_rank, minimum=1)
        if isinstance(self.score, bool) or not isinstance(self.score, (int, float)) or not math.isfinite(self.score):
            raise ValueError("score must be a finite number")
        if not isinstance(self.score_components, Mapping):
            raise ValueError("score_components must be an object")
        for component, value in self.score_components.items():
            _bounded_string("score component", component, minimum=1, maximum=100)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                raise ValueError("score component values must be finite numbers or null")
        object.__setattr__(self, "score_components", dict(self.score_components))
        if self.selection_state not in {"candidate", "selected", "pinned", "excluded"}:
            raise ValueError("unsupported selection_state")
        _bounded_string("selection_reason", self.selection_reason, minimum=1, maximum=500)

    @property
    def is_evidence(self) -> bool:
        return self.selection_state in {"selected", "pinned"}


@dataclass(frozen=True, slots=True)
class CurationOperation:
    operation: str
    citation_id: str
    reason: str | None
    ordinal: int

    def __post_init__(self) -> None:
        if self.operation not in {"include", "exclude", "pin"}:
            raise ValueError("unsupported curation operation")
        if not isinstance(self.citation_id, str) or not _CITATION_ID_RE.fullmatch(self.citation_id):
            raise ValueError("invalid curation citation_id")
        if self.reason is not None:
            _bounded_string("reason", self.reason, minimum=0, maximum=500)
        _bounded_integer("ordinal", self.ordinal, minimum=0)


@dataclass(frozen=True, slots=True)
class DossierRevision:
    schema_version: str
    artifact_type: str
    dossier_key: str
    revision_id: str
    parent_revision_id: str | None
    content_digest: str
    request: ResearchRequest | Mapping[str, Any]
    corpus_context: Mapping[str, Any]
    candidate_evidence: Sequence[EvidenceCandidate | Mapping[str, Any]]
    selected_citation_ids: Sequence[str]
    curation_operations: Sequence[CurationOperation | Mapping[str, Any]]
    derived_context: Mapping[str, Any]
    status: str
    includes_drafts: bool
    warnings: Sequence[str]
    files: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.schema_version != "1.0" or self.artifact_type != "dossier_revision":
            raise ValueError("unsupported dossier contract")
        if not isinstance(self.dossier_key, str) or not _DOSSIER_KEY_RE.fullmatch(self.dossier_key):
            raise ValueError("invalid dossier_key")
        if not isinstance(self.revision_id, str) or not _REVISION_ID_RE.fullmatch(self.revision_id):
            raise ValueError("invalid revision_id")
        if self.parent_revision_id is not None and (
            not isinstance(self.parent_revision_id, str) or not _REVISION_ID_RE.fullmatch(self.parent_revision_id)
        ):
            raise ValueError("invalid parent_revision_id")
        if not isinstance(self.content_digest, str) or not _SHA256_RE.fullmatch(self.content_digest):
            raise ValueError("invalid content_digest")

        request = self.request if isinstance(self.request, ResearchRequest) else ResearchRequest(**dict(self.request))
        object.__setattr__(self, "request", request)
        _validate_corpus_context(self.corpus_context)
        object.__setattr__(self, "corpus_context", dict(self.corpus_context))

        candidates = tuple(
            candidate if isinstance(candidate, EvidenceCandidate) else EvidenceCandidate(**dict(candidate))
            for candidate in self.candidate_evidence
        )
        if len(candidates) > 150:
            raise ValueError("candidate_evidence exceeds 150 items")
        object.__setattr__(self, "candidate_evidence", candidates)

        selected_ids = tuple(self.selected_citation_ids)
        if not 1 <= len(selected_ids) <= 100 or len(selected_ids) != len(set(selected_ids)):
            raise ValueError("selected_citation_ids must contain 1..100 unique items")
        candidate_ids = {candidate.citation.citation_id for candidate in candidates if isinstance(candidate.citation, Citation)}
        if any(not _CITATION_ID_RE.fullmatch(value) or value not in candidate_ids for value in selected_ids):
            raise ValueError("selected citation must resolve into candidate_evidence")
        object.__setattr__(self, "selected_citation_ids", selected_ids)

        operations = tuple(
            operation if isinstance(operation, CurationOperation) else CurationOperation(**dict(operation))
            for operation in self.curation_operations
        )
        if len(operations) > 300:
            raise ValueError("curation_operations exceeds 300 items")
        object.__setattr__(self, "curation_operations", operations)
        _validate_derived_context(self.derived_context)
        object.__setattr__(self, "derived_context", dict(self.derived_context))

        if self.status not in {"ready", "degraded"}:
            raise ValueError("finalized dossier status must be ready or degraded")
        if not isinstance(self.includes_drafts, bool) or self.includes_drafts is not request.includes_drafts:
            raise ValueError("includes_drafts must mirror request visibility")
        _validate_string_sequence("warnings", self.warnings, maximum_items=100, maximum_length=2000)
        object.__setattr__(self, "warnings", tuple(self.warnings))
        _validate_dossier_files(self.files)
        object.__setattr__(self, "files", dict(self.files))


@dataclass(frozen=True, slots=True)
class ValidationResult:
    schema_version: str
    artifact_type: str
    target_type: str
    target_id: str
    target_digest: str
    status: str
    schema_valid: bool
    package_integrity: bool
    dossier_current: bool
    citations_resolved: bool
    coverage_complete: bool
    human_reviewed: bool
    citations: Sequence[Mapping[str, Any]]
    warnings: Sequence[str]
    errors: Sequence[str]
    validated_at: str

    def __post_init__(self) -> None:
        if self.schema_version != "1.0" or self.artifact_type != "validation_result":
            raise ValueError("unsupported validation contract")
        if self.target_type not in {"dossier_revision", "writing_handoff", "writing_output", "imported_writing"}:
            raise ValueError("unsupported validation target_type")
        _bounded_string("target_id", self.target_id, minimum=1, maximum=500)
        if not isinstance(self.target_digest, str) or not _SHA256_RE.fullmatch(self.target_digest):
            raise ValueError("invalid validation target_digest")
        if self.status not in {"valid", "valid_with_warnings", "invalid"}:
            raise ValueError("unsupported validation status")
        for name in (
            "schema_valid",
            "package_integrity",
            "dossier_current",
            "citations_resolved",
            "coverage_complete",
            "human_reviewed",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be boolean")
        if self.human_reviewed:
            raise ValueError("automatic validation cannot set human_reviewed")

        citations = tuple(_validate_citation_result(row) for row in self.citations)
        if len(citations) > 250:
            raise ValueError("validation citations exceeds 250 items")
        resolved = all(row["status"] == "valid" for row in citations)
        if self.citations_resolved is not resolved:
            raise ValueError("citations_resolved does not match per-citation states")
        object.__setattr__(self, "citations", citations)

        _validate_string_sequence("warnings", self.warnings, maximum_items=100, maximum_length=2000)
        _validate_string_sequence("errors", self.errors, maximum_items=100, maximum_length=2000)
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "errors", tuple(self.errors))
        _validate_timestamp("validated_at", self.validated_at, optional=False)

        claims_valid = all(
            (self.schema_valid, self.package_integrity, self.dossier_current, self.citations_resolved, self.coverage_complete)
        )
        if self.status == "valid" and (not claims_valid or self.warnings or self.errors):
            raise ValueError("valid status requires all claims and no warnings or errors")
        if self.status == "valid_with_warnings" and (not claims_valid or not self.warnings or self.errors):
            raise ValueError("valid_with_warnings requires valid claims, warnings and no errors")
        if self.status == "invalid" and not self.errors:
            raise ValueError("invalid status requires at least one error")


def _bounded_integer(name: str, value: Any, *, minimum: int, maximum: int | None = None) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        upper = f"..{maximum}" if maximum is not None else " or greater"
        raise ValueError(f"{name} must be {minimum}{upper}")


def _bounded_string(name: str, value: Any, *, minimum: int, maximum: int) -> None:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise ValueError(f"{name} must be a string of length {minimum}..{maximum}")


def _optional_bounded_string(name: str, value: Any, *, maximum: int) -> None:
    if value is not None:
        _bounded_string(name, value, minimum=1, maximum=maximum)


def _parse_calendar_date(name: str, value: Any) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise ValueError(f"{name} must be YYYY-MM-DD or null")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a valid calendar date") from error


def _validate_timestamp(name: str, value: Any, *, optional: bool = True) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{name} must be an RFC 3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{name} must be an RFC 3339 UTC timestamp") from error
    if parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be UTC")


def _validate_http_url(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError("url must be an HTTP(S) URL or null")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("url must be a credential-free HTTP(S) URL")


def _validate_corpus_context(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("corpus_context must be an object")
    required = {
        "database",
        "built_at",
        "embedding_model",
        "embedding_dimension",
        "retrieval_min_similarity",
        "latest_import_run_key",
        "latest_index_runs",
        "git_revision",
        "warnings",
    }
    if set(value) != required:
        raise ValueError("corpus_context fields do not match contract")
    _bounded_string("database", value["database"], minimum=1, maximum=500)
    _validate_timestamp("built_at", value["built_at"], optional=False)
    _bounded_string("embedding_model", value["embedding_model"], minimum=1, maximum=500)
    _bounded_integer("embedding_dimension", value["embedding_dimension"], minimum=1)
    similarity = value["retrieval_min_similarity"]
    if isinstance(similarity, bool) or not isinstance(similarity, (int, float)) or not -1 <= similarity <= 1:
        raise ValueError("retrieval_min_similarity must be within -1..1")
    _optional_bounded_string("latest_import_run_key", value["latest_import_run_key"], maximum=500)
    if not isinstance(value["latest_index_runs"], Mapping):
        raise ValueError("latest_index_runs must be an object")
    _optional_bounded_string("git_revision", value["git_revision"], maximum=500)
    _validate_string_sequence("corpus context warnings", value["warnings"], maximum_items=100, maximum_length=2000)


def _validate_derived_context(value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != {"topics", "leads"}:
        raise ValueError("derived_context must contain only topics and leads")
    for name in ("topics", "leads"):
        rows = value[name]
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or len(rows) > 100:
            raise ValueError(f"derived_context.{name} must be a bounded array")
        if any(not isinstance(row, Mapping) for row in rows):
            raise ValueError(f"derived_context.{name} rows must be objects")


def _validate_dossier_files(value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != {"dossier", "validation"}:
        raise ValueError("files must contain dossier and validation digests")
    for digest in value.values():
        if not isinstance(digest, Mapping) or set(digest) != {"path", "sha256", "bytes"}:
            raise ValueError("file digest fields do not match contract")
        path = digest["path"]
        if not isinstance(path, str) or not path or "/" in path or "\\" in path:
            raise ValueError("file digest path must be a relative file name")
        sha256 = digest["sha256"]
        if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
            raise ValueError("invalid file digest sha256")
        _bounded_integer("file digest bytes", digest["bytes"], minimum=0)


def _validate_citation_result(value: Any) -> JsonObject:
    if not isinstance(value, Mapping) or set(value) != {"citation_id", "status", "reason"}:
        raise ValueError("citation validation fields do not match contract")
    citation_id = value["citation_id"]
    if not isinstance(citation_id, str) or not _CITATION_ID_RE.fullmatch(citation_id):
        raise ValueError("invalid validation citation_id")
    status = value["status"]
    if status not in {"valid", "missing", "changed", "hidden"}:
        raise ValueError("unsupported citation validation state")
    reason = value["reason"]
    if reason is not None:
        _bounded_string("citation validation reason", reason, minimum=0, maximum=2000)
    return {"citation_id": citation_id, "status": status, "reason": reason}


def _validate_string_sequence(name: str, value: Any, *, maximum_items: int, maximum_length: int) -> None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) > maximum_items:
        raise ValueError(f"{name} must be a bounded array")
    for item in value:
        _bounded_string(name, item, minimum=1, maximum=maximum_length)


def _sha256_json(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
