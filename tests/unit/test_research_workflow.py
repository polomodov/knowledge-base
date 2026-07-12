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
    fuse_and_select_candidates,
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


def _grounded_chunk(
    citation_builder: Callable[..., JsonObject],
    *,
    document: str,
    ordinal: int = 0,
    graph_lead_score: float | None = None,
    **overrides: Any,
) -> JsonObject:
    excerpt = f"Synthetic grounded excerpt for {document}, fragment {ordinal}."
    citation = citation_builder(
        canonical_id=f"canonical-{document}",
        document_key=f"doc-{document}",
        chunk_key=f"chunk-{document}-{ordinal}",
        chunk_ordinal=ordinal,
        char_start=ordinal * 100,
        char_end=ordinal * 100 + len(excerpt),
        excerpt=excerpt,
        title=f"Synthetic {document}",
        url=f"https://example.test/{document}",
        **overrides,
    )
    for derived_field in ("citation_id", "identity_sha256", "excerpt_sha256", "projection_version"):
        citation.pop(derived_field)
    citation["graph_lead_score"] = graph_lead_score
    return citation


def _signal(grounded: JsonObject, score: float) -> JsonObject:
    return {
        "document_key": grounded["document_key"],
        "chunk_key": grounded["chunk_key"],
        "score": score,
    }


def _selection_request(
    research_request_builder: Callable[..., JsonObject],
    *,
    documents: int,
    fragments: int,
    evidence: int,
    candidates: int,
    weights: tuple[float, float] = (1.0, 1.0),
) -> ResearchRequest:
    return ResearchRequest(
        **research_request_builder(
            document_limit=documents,
            fragments_per_document=fragments,
            evidence_limit=evidence,
            candidate_limit=candidates,
            retrieval={
                "mode": "hybrid-chunk-v1",
                "lexical_weight": weights[0],
                "vector_weight": weights[1],
                "tie_policy": "score-desc-citation-id-asc",
            },
        ),
    )


def test_fusion_projects_citation_deduplicates_identity_and_keeps_max_signals(
    research_request_builder: Callable[..., JsonObject],
    citation_builder: Callable[..., JsonObject],
) -> None:
    first_import = _grounded_chunk(citation_builder, document="duplicate", graph_lead_score=0.2)
    second_import = _grounded_chunk(
        citation_builder,
        document="duplicate",
        graph_lead_score=0.6,
        raw_snapshot_key="raw-synthetic-second-run",
        import_run_key="import-synthetic-second-run",
        captured_at="2026-02-01T10:00:00Z",
    )
    request = _selection_request(
        research_request_builder,
        documents=1,
        fragments=1,
        evidence=1,
        candidates=4,
        weights=(0.25, 0.75),
    )

    result = fuse_and_select_candidates(
        request=request,
        lexical=[_signal(first_import, 0.2), _signal(first_import, 0.8)],
        semantic=[_signal(second_import, 0.4), _signal(second_import, 0.7)],
        grounded=[second_import, first_import],
    )

    expected = citation_builder(**{key: value for key, value in first_import.items() if key != "graph_lead_score"})
    assert result == tuple(result)
    assert len(result) == 1
    assert result[0].citation == Citation(**expected)
    assert result[0].score == pytest.approx(0.25 * 0.8 + 0.75 * 0.7)
    assert result[0].score_components == {"lexical": 0.8, "vector": 0.7, "graph_lead": 0.6}
    assert result[0].selection_state == "selected"
    assert result[0].selection_reason == "automatic-round-1"


def test_equal_fused_scores_use_citation_id_tie_break_independent_of_input_order(
    research_request_builder: Callable[..., JsonObject],
    citation_builder: Callable[..., JsonObject],
) -> None:
    grounded = [_grounded_chunk(citation_builder, document=f"tie-{index}") for index in range(3)]
    lexical = [_signal(row, 0.5) for row in grounded]
    semantic = [_signal(row, 0.5) for row in grounded]
    request = _selection_request(
        research_request_builder,
        documents=3,
        fragments=1,
        evidence=3,
        candidates=3,
    )

    forward = fuse_and_select_candidates(request=request, lexical=lexical, semantic=semantic, grounded=grounded)
    reversed_inputs = fuse_and_select_candidates(
        request=request,
        lexical=list(reversed(lexical)),
        semantic=list(reversed(semantic)),
        grounded=list(reversed(grounded)),
    )

    forward_ids = [candidate.citation.citation_id for candidate in forward]
    assert forward_ids == sorted(forward_ids)
    assert forward == reversed_inputs


def test_selection_enforces_all_caps_and_round_robin_with_stable_order(
    research_request_builder: Callable[..., JsonObject],
    citation_builder: Callable[..., JsonObject],
) -> None:
    grounded = [
        _grounded_chunk(citation_builder, document="dominant", ordinal=0),
        _grounded_chunk(citation_builder, document="dominant", ordinal=1),
        _grounded_chunk(citation_builder, document="dominant", ordinal=2),
        _grounded_chunk(citation_builder, document="second"),
        _grounded_chunk(citation_builder, document="third"),
        _grounded_chunk(citation_builder, document="fourth"),
    ]
    scores = [0.99, 0.98, 0.97, 0.80, 0.70, 0.60]
    lexical = [_signal(row, score) for row, score in zip(grounded, scores, strict=True)]
    request = _selection_request(
        research_request_builder,
        documents=3,
        fragments=2,
        evidence=4,
        candidates=5,
    )

    forward = fuse_and_select_candidates(
        request=request,
        lexical=lexical,
        semantic=[],
        grounded=grounded,
    )
    reversed_inputs = fuse_and_select_candidates(
        request=request,
        lexical=list(reversed(lexical)),
        semantic=[],
        grounded=list(reversed(grounded)),
    )

    selected = [candidate for candidate in forward if candidate.is_evidence]
    assert len(forward) == request.candidate_limit == 5
    assert len(selected) == request.evidence_limit == 4
    assert [(row.citation.document_key, row.citation.chunk_ordinal) for row in selected] == [
        ("doc-dominant", 0),
        ("doc-second", 0),
        ("doc-third", 0),
        ("doc-dominant", 1),
    ]
    assert [(row.document_rank, row.fragment_rank) for row in selected] == [(1, 1), (2, 1), (3, 1), (1, 2)]
    assert [row.selection_reason for row in selected] == [
        "automatic-round-1",
        "automatic-round-1",
        "automatic-round-1",
        "automatic-round-2",
    ]
    assert next(row for row in forward if row.citation.chunk_ordinal == 2).selection_state == "candidate"
    assert forward == reversed_inputs


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
