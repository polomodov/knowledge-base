from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from knowledge_base.research_workflow import (
    Citation,
    CurationOperation,
    DossierRevision,
    EvidenceCandidate,
    ResearchRequest,
    ResearchVisibility,
    ValidationResult,
)

JsonObject = dict[str, Any]


def test_research_visibility_exposes_exact_document_status_scope() -> None:
    assert ResearchVisibility.PUBLISHED_ONLY.value == "published_only"
    assert ResearchVisibility.PUBLISHED_ONLY.document_statuses == ("published",)
    assert ResearchVisibility.PUBLISHED_ONLY.includes_drafts is False

    assert ResearchVisibility.PUBLISHED_AND_DRAFTS.value == "published_and_drafts"
    assert ResearchVisibility.PUBLISHED_AND_DRAFTS.document_statuses == ("published", "draft")
    assert ResearchVisibility.PUBLISHED_AND_DRAFTS.includes_drafts is True


def test_research_request_trims_query_derives_caps_and_visibility(
    research_request_builder: Callable[..., JsonObject],
) -> None:
    payload = research_request_builder(
        query="  synthetic systems research  ",
        visibility="published_and_drafts",
        document_limit=4,
        fragments_per_document=3,
    )
    payload.pop("evidence_limit")
    payload.pop("candidate_limit")

    request = ResearchRequest(**payload)

    assert request.query == "synthetic systems research"
    assert request.visibility is ResearchVisibility.PUBLISHED_AND_DRAFTS
    assert request.document_statuses == ("published", "draft")
    assert request.includes_drafts is True
    assert request.evidence_limit == 12
    assert request.candidate_limit == 36


def test_research_request_converts_inclusive_dates_to_utc_half_open_range(
    research_request_builder: Callable[..., JsonObject],
) -> None:
    request = ResearchRequest(
        **research_request_builder(
            published_from="2026-12-30",
            published_to="2026-12-31",
        ),
    )

    assert request.published_from_utc == "2026-12-30T00:00:00Z"
    assert request.published_to_exclusive_utc == "2027-01-01T00:00:00Z"


@pytest.mark.parametrize(
    "overrides",
    [
        {"query": "   "},
        {"query": "я" * 1001},
        {"document_limit": 0},
        {"document_limit": 51},
        {"fragments_per_document": 0},
        {"fragments_per_document": 6},
        {"evidence_limit": 0},
        {"evidence_limit": 101},
        {"candidate_limit": 0},
        {"candidate_limit": 151},
        {"evidence_limit": 37, "candidate_limit": 36},
        {"published_from": "2026-02-30"},
        {"published_from": "2026-03-02", "published_to": "2026-03-01"},
        {"visibility": "all_documents"},
    ],
)
def test_research_request_rejects_invalid_bounds(
    research_request_builder: Callable[..., JsonObject],
    overrides: JsonObject,
) -> None:
    with pytest.raises(ValueError):
        ResearchRequest(**research_request_builder(**overrides))


def test_citation_accepts_verified_identity_and_excludes_run_provenance_from_identity(
    citation_builder: Callable[..., JsonObject],
) -> None:
    first = Citation(**citation_builder())
    reimported = Citation(
        **citation_builder(
            raw_snapshot_key="raw-synthetic-second-run",
            import_run_key="import-synthetic-second-run",
            captured_at="2026-02-01T10:00:00Z",
        ),
    )

    assert first.citation_id.startswith("cit-")
    assert len(first.citation_id) == 20
    assert first.identity_sha256 == reimported.identity_sha256
    assert first.citation_id == reimported.citation_id


@pytest.mark.parametrize(
    "overrides",
    [
        {"char_start": -1},
        {"char_end": 0},
        {"offset_basis": "bytes_v1"},
        {"excerpt_sha256": "0" * 64},
        {"identity_sha256": "0" * 64},
        {"citation_id": "cit-0000000000000000"},
    ],
)
def test_citation_rejects_invalid_offsets_hashes_and_identity(
    citation_builder: Callable[..., JsonObject],
    overrides: JsonObject,
) -> None:
    with pytest.raises(ValueError):
        Citation(**citation_builder(**overrides))


@pytest.mark.parametrize(
    ("state", "is_evidence"),
    [("candidate", False), ("selected", True), ("pinned", True), ("excluded", False)],
)
def test_evidence_candidate_selection_states(
    citation_builder: Callable[..., JsonObject],
    evidence_candidate_builder: Callable[..., JsonObject],
    state: str,
    is_evidence: bool,
) -> None:
    payload = evidence_candidate_builder(selection_state=state)
    payload["citation"] = Citation(**citation_builder())

    candidate = EvidenceCandidate(**payload)

    assert candidate.selection_state == state
    assert candidate.is_evidence is is_evidence


@pytest.mark.parametrize(
    "overrides",
    [
        {"document_rank": 0},
        {"fragment_rank": 0},
        {"score": float("nan")},
        {"selection_state": "hidden"},
        {"selection_reason": ""},
    ],
)
def test_evidence_candidate_rejects_invalid_shape(
    citation_builder: Callable[..., JsonObject],
    evidence_candidate_builder: Callable[..., JsonObject],
    overrides: JsonObject,
) -> None:
    payload = evidence_candidate_builder(**overrides)
    payload["citation"] = Citation(**citation_builder())
    with pytest.raises(ValueError):
        EvidenceCandidate(**payload)


@pytest.mark.parametrize("operation", ["include", "exclude", "pin"])
def test_curation_operation_accepts_basic_shape(operation: str) -> None:
    result = CurationOperation(operation=operation, citation_id="cit-0123456789abcdef", reason=None, ordinal=0)
    assert result.operation == operation


@pytest.mark.parametrize(
    "payload",
    [
        {"operation": "replace", "citation_id": "cit-0123456789abcdef", "reason": None, "ordinal": 0},
        {"operation": "include", "citation_id": "", "reason": None, "ordinal": 0},
        {"operation": "include", "citation_id": "cit-0123456789abcdef", "reason": "x" * 501, "ordinal": 0},
        {"operation": "include", "citation_id": "cit-0123456789abcdef", "reason": None, "ordinal": -1},
    ],
)
def test_curation_operation_rejects_invalid_shape(payload: JsonObject) -> None:
    with pytest.raises(ValueError):
        CurationOperation(**payload)


@pytest.mark.parametrize("status", ["ready", "degraded"])
def test_dossier_revision_allows_only_finalized_statuses(
    dossier_manifest_builder: Callable[..., JsonObject],
    status: str,
) -> None:
    assert DossierRevision(**dossier_manifest_builder(status=status)).status == status


def test_dossier_revision_rejects_invalid_manifest_status(
    dossier_manifest_builder: Callable[..., JsonObject],
) -> None:
    with pytest.raises(ValueError):
        DossierRevision(**dossier_manifest_builder(status="invalid"))


def _validation_payload(*, status: str = "valid", citation_status: str = "valid") -> JsonObject:
    resolved = citation_status == "valid"
    return {
        "schema_version": "1.0",
        "artifact_type": "validation_result",
        "target_type": "dossier_revision",
        "target_id": "rev-20260712T120000Z-01234567",
        "target_digest": "a" * 64,
        "status": status,
        "schema_valid": True,
        "package_integrity": True,
        "dossier_current": resolved,
        "citations_resolved": resolved,
        "coverage_complete": True,
        "human_reviewed": False,
        "citations": [{"citation_id": "cit-0123456789abcdef", "status": citation_status, "reason": None}],
        "warnings": [] if status == "valid" else ["synthetic validation warning"],
        "errors": [] if status != "invalid" else ["synthetic citation rejection"],
        "validated_at": "2026-07-12T12:00:00Z",
    }


@pytest.mark.parametrize("citation_status", ["missing", "changed", "hidden"])
def test_validation_result_supports_all_non_resolved_citation_states(citation_status: str) -> None:
    result = ValidationResult(**_validation_payload(status="invalid", citation_status=citation_status))
    assert result.status == "invalid"
    assert result.citations_resolved is False
    assert result.citations[0]["status"] == citation_status


def test_validation_result_accepts_valid_and_warning_aggregates() -> None:
    valid = ValidationResult(**_validation_payload())
    warned = ValidationResult(**_validation_payload(status="valid_with_warnings"))
    assert valid.status == "valid"
    assert warned.status == "valid_with_warnings"


def test_validation_result_rejects_unknown_citation_state_and_automatic_human_review() -> None:
    unknown = _validation_payload(status="invalid", citation_status="unavailable")
    reviewed = _validation_payload()
    reviewed["human_reviewed"] = True

    with pytest.raises(ValueError):
        ValidationResult(**unknown)
    with pytest.raises(ValueError):
        ValidationResult(**reviewed)
