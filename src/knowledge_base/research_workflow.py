from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast

from knowledge_base.arango import ArangoError
from knowledge_base.research_artifacts import (
    ShortIdRegistry,
    canonical_json_bytes,
    canonical_sha256,
    safe_http_url,
)
from knowledge_base.research_retrieval import (
    ResearchRetrievalError,
    clean_community_leads,
    hydrate_current_citations,
    lexical_chunk_candidates,
    load_corpus_context,
    related_leads,
    semantic_chunk_candidates,
    topic_leads,
)

if TYPE_CHECKING:
    from knowledge_base.embeddings import EmbeddingProvider
    from knowledge_base.repository import KnowledgeRepository

JsonObject = dict[str, Any]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CITATION_ID_RE = re.compile(r"^cit-[0-9a-f]{16}$")
_DOSSIER_KEY_RE = re.compile(r"^research-[a-z0-9_-]+-[0-9a-f]{12}$")
_REVISION_ID_RE = re.compile(r"^rev-(?a:\d{8}T\d{6}Z)-[0-9a-f]{8}$")
_DATE_RE = re.compile(r"^(?a:\d{4}-\d{2}-\d{2})$")
_UTC_TIMESTAMP_RE = re.compile(r"^(?a:\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)$")


class ResearchVisibility(StrEnum):
    PUBLISHED_ONLY = "published_only"
    PUBLISHED_AND_DRAFTS = "published_and_drafts"

    @property
    def document_statuses(self) -> tuple[str, ...]:
        statuses = ["published"]
        if self is ResearchVisibility.PUBLISHED_AND_DRAFTS:
            statuses.append("draft")
        return tuple(statuses)

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
class DossierBuildResult:
    status: str
    request: ResearchRequest | Mapping[str, Any]
    candidate_evidence: Sequence[EvidenceCandidate | Mapping[str, Any]]
    selected_citation_ids: Sequence[str]
    corpus_context: Mapping[str, Any]
    derived_context: Mapping[str, Sequence[Mapping[str, Any]]]
    includes_drafts: bool
    warnings: Sequence[str]

    def __post_init__(self) -> None:
        if self.status not in {"ready", "degraded", "no_evidence"}:
            raise ValueError("unsupported dossier build status")
        request = self.request if isinstance(self.request, ResearchRequest) else ResearchRequest(**dict(self.request))
        object.__setattr__(self, "request", request)

        candidates = tuple(
            candidate if isinstance(candidate, EvidenceCandidate) else EvidenceCandidate(**dict(candidate))
            for candidate in self.candidate_evidence
        )
        candidate_limit = request.candidate_limit
        assert candidate_limit is not None
        if len(candidates) > candidate_limit:
            raise ValueError("dossier build exceeds request candidate limit")
        object.__setattr__(self, "candidate_evidence", candidates)

        expected_selected = tuple(
            candidate.citation.citation_id
            for candidate in candidates
            if candidate.is_evidence and isinstance(candidate.citation, Citation)
        )
        selected_ids = tuple(self.selected_citation_ids)
        if selected_ids != expected_selected:
            raise ValueError("selected_citation_ids must match selected candidate order")
        object.__setattr__(self, "selected_citation_ids", selected_ids)

        _validate_corpus_context(self.corpus_context)
        object.__setattr__(self, "corpus_context", dict(self.corpus_context))
        _validate_derived_context(self.derived_context)
        object.__setattr__(
            self,
            "derived_context",
            {
                "topics": tuple(dict(row) for row in self.derived_context["topics"]),
                "leads": tuple(dict(row) for row in self.derived_context["leads"]),
            },
        )
        if not isinstance(self.includes_drafts, bool) or self.includes_drafts is not request.includes_drafts:
            raise ValueError("includes_drafts must mirror request visibility")
        _validate_string_sequence("dossier build warnings", self.warnings, maximum_items=100, maximum_length=2000)
        warnings = tuple(self.warnings)
        if len(warnings) != len(set(warnings)):
            raise ValueError("dossier build warnings must be unique")
        object.__setattr__(self, "warnings", warnings)

        if self.status == "no_evidence":
            if candidates or selected_ids or any(self.derived_context.values()):
                raise ValueError("no_evidence build cannot contain candidates or derived context")
        elif not selected_ids:
            raise ValueError("publishable dossier build requires selected evidence")

    @property
    def publishable(self) -> bool:
        return self.status in {"ready", "degraded"}


@dataclass(slots=True)
class _CandidateAccumulator:
    citation: Citation
    representative_key: bytes
    lexical: float | None
    vector: float | None
    graph_lead: float | None

    def merge(
        self,
        *,
        citation: Citation,
        representative_key: bytes,
        lexical: float | None,
        vector: float | None,
        graph_lead: float | None,
    ) -> None:
        if representative_key < self.representative_key:
            self.citation = citation
            self.representative_key = representative_key
        self.lexical = _max_optional(self.lexical, lexical)
        self.vector = _max_optional(self.vector, vector)
        self.graph_lead = _max_optional(self.graph_lead, graph_lead)


@dataclass(frozen=True, slots=True)
class _ScoredCandidate:
    citation: Citation
    score: float
    lexical: float | None
    vector: float | None
    graph_lead: float | None


def fuse_and_select_candidates(
    *,
    request: ResearchRequest | Mapping[str, Any],
    lexical: Iterable[Mapping[str, Any]],
    semantic: Iterable[Mapping[str, Any]],
    grounded: Iterable[Mapping[str, Any]],
) -> tuple[EvidenceCandidate, ...]:
    """Project, fuse and deterministically select grounded chunk evidence."""

    effective_request = request if isinstance(request, ResearchRequest) else ResearchRequest(**dict(request))
    lexical_weight = _retrieval_weight(effective_request, "lexical_weight")
    vector_weight = _retrieval_weight(effective_request, "vector_weight")
    tie_policy = effective_request.retrieval.get("tie_policy", "score-desc-citation-id-asc")
    if tie_policy != "score-desc-citation-id-asc":
        raise ValueError("unsupported research candidate tie_policy")

    lexical_scores = _collect_signal_scores("lexical", lexical)
    vector_scores = _collect_signal_scores("semantic", semantic)
    accumulators = _accumulate_grounded_candidates(
        grounded=grounded,
        lexical_scores=lexical_scores,
        vector_scores=vector_scores,
    )
    ranked = _rank_accumulated_candidates(
        accumulators,
        lexical_weight=lexical_weight,
        vector_weight=vector_weight,
        candidate_limit=effective_request.candidate_limit,
    )
    if not ranked:
        return ()

    by_document = _group_candidates_by_document(ranked)
    document_order = _rank_documents(by_document)
    document_rank = {document_key: index for index, document_key in enumerate(document_order, start=1)}
    fragment_rank = {
        row.citation.identity_sha256: index for rows in by_document.values() for index, row in enumerate(rows, start=1)
    }
    selected, selected_round = _select_candidate_rounds(
        by_document,
        document_order=document_order,
        document_limit=effective_request.document_limit,
        fragments_per_document=effective_request.fragments_per_document,
        evidence_limit=effective_request.evidence_limit,
    )

    selected_ids = set(selected_round)
    output_order = [*selected, *(row for row in ranked if row.citation.identity_sha256 not in selected_ids)]
    return _materialize_evidence_candidates(
        output_order,
        document_rank=document_rank,
        fragment_rank=fragment_rank,
        selected_round=selected_round,
    )


def _accumulate_grounded_candidates(
    *,
    grounded: Iterable[Mapping[str, Any]],
    lexical_scores: Mapping[tuple[str, str], float],
    vector_scores: Mapping[tuple[str, str], float],
) -> dict[str, _CandidateAccumulator]:
    citation_ids = ShortIdRegistry(prefix="cit", length=16)
    accumulators: dict[str, _CandidateAccumulator] = {}
    for grounded_row in grounded:
        if not isinstance(grounded_row, Mapping):
            raise ValueError("grounded candidates must be objects")
        graph_lead = _finite_number("graph_lead_score", grounded_row.get("graph_lead_score"), optional=True)
        citation, representative_key = _project_citation(grounded_row, citation_ids)
        signal_key = (citation.document_key, citation.chunk_key)
        if signal_key not in lexical_scores and signal_key not in vector_scores:
            continue
        _merge_grounded_candidate(
            accumulators,
            citation=citation,
            representative_key=representative_key,
            lexical_score=lexical_scores.get(signal_key),
            vector_score=vector_scores.get(signal_key),
            graph_lead=graph_lead,
        )
    return accumulators


def _merge_grounded_candidate(
    accumulators: dict[str, _CandidateAccumulator],
    *,
    citation: Citation,
    representative_key: bytes,
    lexical_score: float | None,
    vector_score: float | None,
    graph_lead: float | None,
) -> None:
    accumulator = accumulators.get(citation.identity_sha256)
    if accumulator is None:
        accumulators[citation.identity_sha256] = _CandidateAccumulator(
            citation=citation,
            representative_key=representative_key,
            lexical=lexical_score,
            vector=vector_score,
            graph_lead=graph_lead,
        )
        return
    accumulator.merge(
        citation=citation,
        representative_key=representative_key,
        lexical=lexical_score,
        vector=vector_score,
        graph_lead=graph_lead,
    )


def _rank_accumulated_candidates(
    accumulators: Mapping[str, _CandidateAccumulator],
    *,
    lexical_weight: float,
    vector_weight: float,
    candidate_limit: int | None,
) -> list[_ScoredCandidate]:
    return sorted(
        (
            _ScoredCandidate(
                citation=row.citation,
                score=lexical_weight * (row.lexical or 0.0) + vector_weight * (row.vector or 0.0),
                lexical=row.lexical,
                vector=row.vector,
                graph_lead=row.graph_lead,
            )
            for row in accumulators.values()
        ),
        key=lambda row: (-row.score, row.citation.citation_id),
    )[:candidate_limit]


def _group_candidates_by_document(
    ranked: Iterable[_ScoredCandidate],
) -> dict[str, list[_ScoredCandidate]]:
    by_document: dict[str, list[_ScoredCandidate]] = {}
    for row in ranked:
        by_document.setdefault(row.citation.document_key, []).append(row)
    return by_document


def _rank_documents(by_document: Mapping[str, Sequence[_ScoredCandidate]]) -> list[str]:
    return sorted(
        by_document,
        key=lambda document_key: (
            -by_document[document_key][0].score,
            by_document[document_key][0].citation.citation_id,
        ),
    )


def _select_candidate_rounds(
    by_document: Mapping[str, Sequence[_ScoredCandidate]],
    *,
    document_order: Sequence[str],
    document_limit: int,
    fragments_per_document: int,
    evidence_limit: int | None,
) -> tuple[list[_ScoredCandidate], dict[str, int]]:
    selected: list[_ScoredCandidate] = []
    selected_round: dict[str, int] = {}
    eligible_documents = document_order[:document_limit]
    for round_index in range(fragments_per_document):
        for document_key in eligible_documents:
            document_rows = by_document[document_key]
            if round_index >= len(document_rows):
                continue
            row = document_rows[round_index]
            selected.append(row)
            selected_round[row.citation.identity_sha256] = round_index + 1
            if len(selected) == evidence_limit:
                break
        if len(selected) == evidence_limit:
            break
    return selected, selected_round


def _materialize_evidence_candidates(
    output_order: Iterable[_ScoredCandidate],
    *,
    document_rank: Mapping[str, int],
    fragment_rank: Mapping[str, int],
    selected_round: Mapping[str, int],
) -> tuple[EvidenceCandidate, ...]:
    selected_ids = set(selected_round)
    return tuple(
        EvidenceCandidate(
            citation=row.citation,
            document_rank=document_rank[row.citation.document_key],
            fragment_rank=fragment_rank[row.citation.identity_sha256],
            score=row.score,
            score_components={"lexical": row.lexical, "vector": row.vector, "graph_lead": row.graph_lead},
            selection_state="selected" if row.citation.identity_sha256 in selected_ids else "candidate",
            selection_reason=(
                f"automatic-round-{selected_round[row.citation.identity_sha256]}"
                if row.citation.identity_sha256 in selected_ids
                else "candidate-pool"
            ),
        )
        for row in output_order
    )


class DossierBuildError(RuntimeError):
    """A required dossier read or evidence projection failed safely."""


class DossierValidationError(RuntimeError):
    """Current-corpus dossier validation failed without a citation verdict."""


class DossierCurationError(RuntimeError):
    """A dossier curation request was rejected without exposing private details."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        parent_validation: ValidationResult | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.parent_validation = parent_validation


_DRAFT_SCOPE_WARNING = "draft_visibility_enabled"
_CONTEXT_WARNING = "optional corpus/index freshness context is unavailable"
_TOPIC_WARNING = "optional topic context is unavailable"
_RELATED_WARNING = "optional related context is unavailable"
_COMMUNITY_WARNING = "optional community context is unavailable"


def build_dossier(
    repository: KnowledgeRepository,
    request: ResearchRequest | Mapping[str, Any],
    *,
    provider: EmbeddingProvider,
    built_at: str,
    git_revision: str | None = None,
) -> DossierBuildResult:
    """Build one deterministic, read-only dossier candidate result in memory."""

    effective_request = request if isinstance(request, ResearchRequest) else ResearchRequest(**dict(request))
    try:
        lexical_rows = lexical_chunk_candidates(repository, effective_request)
        semantic_rows, semantic_warnings = semantic_chunk_candidates(
            repository, effective_request, provider=provider
        )
    except (ArangoError, ResearchRetrievalError) as error:
        raise DossierBuildError("required dossier evidence retrieval failed") from error

    try:
        lexical_signals = [_retrieval_signal(row, "lexical") for row in lexical_rows]
        semantic_signals = [_retrieval_signal(row, "vector") for row in semantic_rows]
        grounded = [_grounded_projection(row) for row in (*lexical_rows, *semantic_rows)]
        _assert_consistent_grounded_identities(grounded)
        candidates = fuse_and_select_candidates(
            request=effective_request,
            lexical=lexical_signals,
            semantic=semantic_signals,
            grounded=grounded,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise DossierBuildError("retrieved dossier evidence failed allowlisted citation projection") from error

    corpus_context = _load_build_corpus_context(
        repository,
        effective_request,
        provider=provider,
        built_at=built_at,
        git_revision=git_revision,
    )
    context_warnings = tuple(corpus_context["warnings"])
    informational_warnings = (_DRAFT_SCOPE_WARNING,) if effective_request.includes_drafts else ()
    selected = tuple(candidate for candidate in candidates if candidate.is_evidence)
    selected_citations = tuple(_candidate_citation(candidate) for candidate in selected)
    selected_ids = tuple(citation.citation_id for citation in selected_citations)
    if not selected:
        return DossierBuildResult(
            status="no_evidence",
            request=effective_request,
            candidate_evidence=(),
            selected_citation_ids=(),
            corpus_context=corpus_context,
            derived_context={"topics": (), "leads": ()},
            includes_drafts=effective_request.includes_drafts,
            warnings=_stable_unique((*informational_warnings, *context_warnings, *semantic_warnings)),
        )

    document_keys = _stable_unique(citation.document_key for citation in selected_citations)
    chunk_keys = _stable_unique(citation.chunk_key for citation in selected_citations)
    candidate_limit = effective_request.candidate_limit
    assert candidate_limit is not None
    topic_rows, topic_warning = _optional_leads(
        lambda: topic_leads(
            repository,
            document_keys,
            effective_request,
            limit=min(100, candidate_limit),
        ),
        warning=_TOPIC_WARNING,
    )
    related_rows, related_warning = _optional_leads(
        lambda: related_leads(
            repository,
            chunk_keys,
            effective_request,
            limit=min(50, candidate_limit),
        ),
        warning=_RELATED_WARNING,
    )
    community_rows, community_warning = _optional_leads(
        lambda: clean_community_leads(
            repository,
            document_keys,
            effective_request,
            limit=min(50, candidate_limit),
        ),
        warning=_COMMUNITY_WARNING,
    )
    optional_warnings = tuple(warning for warning in (topic_warning, related_warning, community_warning) if warning is not None)
    degradation_warnings = _stable_unique((*context_warnings, *optional_warnings, *semantic_warnings))
    warnings = _stable_unique((*informational_warnings, *degradation_warnings))
    leads = (
        *({**row, "kind": "related_chunk"} for row in related_rows),
        *({**row, "kind": "clean_community"} for row in community_rows),
    )
    return DossierBuildResult(
        status="degraded" if degradation_warnings else "ready",
        request=effective_request,
        candidate_evidence=candidates,
        selected_citation_ids=selected_ids,
        corpus_context=corpus_context,
        derived_context={"topics": topic_rows, "leads": leads},
        includes_drafts=effective_request.includes_drafts,
        warnings=warnings,
    )


def _candidate_citation(candidate: EvidenceCandidate) -> Citation:
    if not isinstance(candidate.citation, Citation):  # pragma: no cover - normalized by EvidenceCandidate
        raise DossierBuildError("candidate citation was not normalized")
    return candidate.citation


def _retrieval_signal(row: Mapping[str, Any], component: str) -> JsonObject:
    chunk = _required_mapping(row, "chunk")
    document = _required_mapping(row, "document")
    components = _required_mapping(row, "score_components")
    return {
        "document_key": document.get("_key"),
        "chunk_key": chunk.get("_key"),
        "score": components.get(component),
    }


def _grounded_projection(row: Mapping[str, Any]) -> JsonObject:
    chunk = _required_mapping(row, "chunk")
    document = _required_mapping(row, "document")
    raw_edge = _required_mapping(row, "raw_edge")
    raw_snapshot = _required_mapping(row, "raw_snapshot")
    source_edge = _required_mapping(row, "source_edge")
    provenance = source_edge.get("provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    title = document.get("title")
    document_url = document.get("url")
    raw_import = raw_edge.get("import_run_key")
    raw_capture = raw_snapshot.get("captured_at")
    return {
        "source_key": document.get("source_key"),
        "canonical_id": document.get("canonical_id"),
        "document_key": document.get("_key"),
        "chunk_key": chunk.get("_key"),
        "chunk_ordinal": chunk.get("ordinal"),
        "char_start": chunk.get("char_start"),
        "char_end": chunk.get("char_end"),
        "offset_basis": "normalized_whitespace_v1",
        "excerpt": chunk.get("text"),
        "title": title if isinstance(title, str) else "",
        "published_at": document.get("published_at"),
        "document_status": document.get("status"),
        "url": document_url if safe_http_url(document_url) is not None else provenance.get("url"),
        "raw_snapshot_key": raw_snapshot.get("_key"),
        "import_run_key": raw_import if isinstance(raw_import, str) and raw_import else source_edge.get("import_run_key"),
        "captured_at": raw_capture if isinstance(raw_capture, str) and raw_capture else provenance.get("captured_at"),
        "graph_lead_score": None,
    }


def _required_mapping(row: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    value = row.get(field)
    if not isinstance(value, Mapping):
        raise ValueError(f"retrieved evidence is missing {field}")
    return value


def _assert_consistent_grounded_identities(grounded: Sequence[Mapping[str, Any]]) -> None:
    identity_by_chunk: dict[tuple[str, str], str] = {}
    for row in grounded:
        document_key = row.get("document_key")
        chunk_key = row.get("chunk_key")
        if not isinstance(document_key, str) or not isinstance(chunk_key, str):
            raise ValueError("grounded evidence requires document and chunk keys")
        identity = canonical_sha256(_citation_identity_projection(row))
        key = (document_key, chunk_key)
        previous = identity_by_chunk.setdefault(key, identity)
        if previous != identity:
            raise DossierBuildError("conflicting citation identity for one retrieved chunk")


def _load_build_corpus_context(
    repository: KnowledgeRepository,
    request: ResearchRequest,
    *,
    provider: EmbeddingProvider,
    built_at: str,
    git_revision: str | None,
) -> JsonObject:
    try:
        context = load_corpus_context(
            repository,
            request,
            provider=provider,
            built_at=built_at,
            git_revision=git_revision,
        )
    except ArangoError:
        settings = repository.client.settings
        context = {
            "database": settings.arango_database,
            "built_at": built_at,
            "embedding_model": provider.model,
            "embedding_dimension": provider.dimension,
            "retrieval_min_similarity": settings.retrieval_min_similarity,
            "latest_import_run_key": None,
            "latest_index_runs": {},
            "git_revision": git_revision,
            "warnings": [_CONTEXT_WARNING],
        }
    _validate_corpus_context(context)
    return dict(context)


def _optional_leads(
    read: Callable[[], Sequence[JsonObject]],
    *,
    warning: str,
) -> tuple[tuple[JsonObject, ...], str | None]:
    try:
        rows = read()
    except ArangoError:
        return (), warning
    return tuple(dict(row) for row in rows), None


def _stable_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _collect_signal_scores(
    name: str,
    signals: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str], float]:
    scores: dict[tuple[str, str], float] = {}
    for signal in signals:
        if not isinstance(signal, Mapping):
            raise ValueError(f"{name} signals must be objects")
        document_key = signal.get("document_key")
        chunk_key = signal.get("chunk_key")
        _bounded_string(f"{name} document_key", document_key, minimum=1, maximum=256)
        _bounded_string(f"{name} chunk_key", chunk_key, minimum=1, maximum=256)
        assert isinstance(document_key, str)
        assert isinstance(chunk_key, str)
        score = _finite_number(f"{name} score", signal.get("score"))
        assert score is not None
        key = (document_key, chunk_key)
        scores[key] = max(scores.get(key, score), score)
    return scores


def _project_citation(
    grounded: Mapping[str, Any],
    citation_ids: ShortIdRegistry,
) -> tuple[Citation, bytes]:
    required = {
        "source_key",
        "canonical_id",
        "document_key",
        "chunk_key",
        "chunk_ordinal",
        "char_start",
        "char_end",
        "offset_basis",
        "excerpt",
        "title",
        "document_status",
    }
    missing = sorted(required - grounded.keys())
    if missing:
        raise ValueError(f"grounded citation is missing fields: {', '.join(missing)}")
    excerpt = grounded["excerpt"]
    if not isinstance(excerpt, str):
        raise ValueError("grounded citation excerpt must be a string")
    identity_projection = _citation_identity_projection(grounded)
    identity_sha256 = canonical_sha256(identity_projection)
    payload: JsonObject = {
        "citation_id": citation_ids.register(identity_sha256),
        "identity_sha256": identity_sha256,
        **identity_projection,
        "chunk_ordinal": grounded["chunk_ordinal"],
        "excerpt": excerpt,
        "title": grounded["title"],
        "published_at": grounded.get("published_at"),
        "document_status": grounded["document_status"],
        "url": safe_http_url(grounded.get("url")),
        "raw_snapshot_key": grounded.get("raw_snapshot_key"),
        "import_run_key": grounded.get("import_run_key"),
        "captured_at": grounded.get("captured_at"),
    }
    return Citation(**payload), canonical_json_bytes(payload)


def _citation_identity_projection(grounded: Mapping[str, Any]) -> JsonObject:
    excerpt = grounded.get("excerpt")
    if not isinstance(excerpt, str):
        raise ValueError("grounded citation excerpt must be a string")
    return {
        "projection_version": "citation-v1",
        "source_key": grounded["source_key"],
        "canonical_id": grounded["canonical_id"],
        "document_key": grounded["document_key"],
        "chunk_key": grounded["chunk_key"],
        "char_start": grounded["char_start"],
        "char_end": grounded["char_end"],
        "offset_basis": grounded["offset_basis"],
        "excerpt_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
    }


def _retrieval_weight(request: ResearchRequest, name: str) -> float:
    weight = _finite_number(name, request.retrieval.get(name, 1.0))
    assert weight is not None
    if weight < 0:
        raise ValueError(f"{name} must be non-negative")
    return weight


def _finite_number(name: str, value: Any, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        suffix = " or null" if optional else ""
        raise ValueError(f"{name} must be a finite number{suffix}")
    return float(value)


def _max_optional(left: float | None, right: float | None) -> float | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


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
        _validate_dossier_revision_identity(self)

        request = self.request if isinstance(self.request, ResearchRequest) else ResearchRequest(**dict(self.request))
        object.__setattr__(self, "request", request)
        _validate_corpus_context(self.corpus_context)
        object.__setattr__(self, "corpus_context", dict(self.corpus_context))

        candidates = _normalize_dossier_candidates(self.candidate_evidence)
        object.__setattr__(self, "candidate_evidence", candidates)

        selected_ids = _normalize_selected_citation_ids(self.selected_citation_ids, candidates)
        object.__setattr__(self, "selected_citation_ids", selected_ids)

        operations = _normalize_curation_operations(self.curation_operations)
        object.__setattr__(self, "curation_operations", operations)
        _validate_derived_context(self.derived_context)
        object.__setattr__(self, "derived_context", dict(self.derived_context))

        _validate_dossier_revision_state(self, request)
        _validate_string_sequence("warnings", self.warnings, maximum_items=100, maximum_length=2000)
        object.__setattr__(self, "warnings", tuple(self.warnings))
        _validate_dossier_files(self.files)
        object.__setattr__(self, "files", dict(self.files))


def _validate_dossier_revision_identity(revision: DossierRevision) -> None:
    if revision.schema_version != "1.0" or revision.artifact_type != "dossier_revision":
        raise ValueError("unsupported dossier contract")
    if not isinstance(revision.dossier_key, str) or not _DOSSIER_KEY_RE.fullmatch(revision.dossier_key):
        raise ValueError("invalid dossier_key")
    if not isinstance(revision.revision_id, str) or not _REVISION_ID_RE.fullmatch(revision.revision_id):
        raise ValueError("invalid revision_id")
    if revision.parent_revision_id is not None and (
        not isinstance(revision.parent_revision_id, str) or not _REVISION_ID_RE.fullmatch(revision.parent_revision_id)
    ):
        raise ValueError("invalid parent_revision_id")
    if not isinstance(revision.content_digest, str) or not _SHA256_RE.fullmatch(revision.content_digest):
        raise ValueError("invalid content_digest")


def _normalize_dossier_candidates(
    candidate_evidence: Sequence[EvidenceCandidate | Mapping[str, Any]],
) -> tuple[EvidenceCandidate, ...]:
    candidates = tuple(
        candidate if isinstance(candidate, EvidenceCandidate) else EvidenceCandidate(**dict(candidate))
        for candidate in candidate_evidence
    )
    if len(candidates) > 150:
        raise ValueError("candidate_evidence exceeds 150 items")
    return candidates


def _normalize_selected_citation_ids(
    selected_citation_ids: Sequence[str],
    candidates: Sequence[EvidenceCandidate],
) -> tuple[str, ...]:
    selected_ids = tuple(selected_citation_ids)
    if not 1 <= len(selected_ids) <= 100 or len(selected_ids) != len(set(selected_ids)):
        raise ValueError("selected_citation_ids must contain 1..100 unique items")
    candidate_ids = {candidate.citation.citation_id for candidate in candidates if isinstance(candidate.citation, Citation)}
    if any(not _CITATION_ID_RE.fullmatch(value) or value not in candidate_ids for value in selected_ids):
        raise ValueError("selected citation must resolve into candidate_evidence")
    return selected_ids


def _normalize_curation_operations(
    curation_operations: Sequence[CurationOperation | Mapping[str, Any]],
) -> tuple[CurationOperation, ...]:
    operations = tuple(
        operation if isinstance(operation, CurationOperation) else CurationOperation(**dict(operation))
        for operation in curation_operations
    )
    if len(operations) > 300:
        raise ValueError("curation_operations exceeds 300 items")
    return operations


def _validate_dossier_revision_state(revision: DossierRevision, request: ResearchRequest) -> None:
    if revision.status not in {"ready", "degraded"}:
        raise ValueError("finalized dossier status must be ready or degraded")
    if not isinstance(revision.includes_drafts, bool) or revision.includes_drafts is not request.includes_drafts:
        raise ValueError("includes_drafts must mirror request visibility")


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
        _validate_validation_result_identity(self)
        _validate_validation_claim_types(self)

        citations = _normalize_validation_citations(self.citations, citations_resolved=self.citations_resolved)
        object.__setattr__(self, "citations", citations)

        _validate_string_sequence("warnings", self.warnings, maximum_items=100, maximum_length=2000)
        _validate_string_sequence("errors", self.errors, maximum_items=100, maximum_length=2000)
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "errors", tuple(self.errors))
        _validate_timestamp("validated_at", self.validated_at, optional=False)

        _validate_validation_result_status(self)


@dataclass(frozen=True, slots=True)
class DossierCurationResult:
    parent_revision_id: str
    request: ResearchRequest | Mapping[str, Any]
    corpus_context: Mapping[str, Any]
    candidate_evidence: Sequence[EvidenceCandidate | Mapping[str, Any]]
    selected_citation_ids: Sequence[str]
    curation_operations: Sequence[CurationOperation | Mapping[str, Any]]
    derived_context: Mapping[str, Any]
    status: str
    includes_drafts: bool
    warnings: Sequence[str]
    parent_validation: ValidationResult

    def __post_init__(self) -> None:
        if not isinstance(self.parent_revision_id, str) or not _REVISION_ID_RE.fullmatch(self.parent_revision_id):
            raise ValueError("invalid parent_revision_id")

        request = self.request if isinstance(self.request, ResearchRequest) else ResearchRequest(**dict(self.request))
        object.__setattr__(self, "request", request)
        _validate_corpus_context(self.corpus_context)
        object.__setattr__(self, "corpus_context", dict(self.corpus_context))

        candidates = _normalize_dossier_candidates(self.candidate_evidence)
        object.__setattr__(self, "candidate_evidence", candidates)
        selected_ids = _normalize_selected_citation_ids(self.selected_citation_ids, candidates)
        expected_selected_ids = tuple(
            candidate.citation.citation_id
            for state in ("pinned", "selected")
            for candidate in candidates
            if candidate.selection_state == state and isinstance(candidate.citation, Citation)
        )
        if selected_ids != expected_selected_ids:
            raise ValueError("selected_citation_ids must present pinned then selected evidence in stable order")
        object.__setattr__(self, "selected_citation_ids", selected_ids)

        operations = _normalize_curation_operations(self.curation_operations)
        if not operations:
            raise ValueError("curation result requires at least one operation")
        object.__setattr__(self, "curation_operations", operations)
        _validate_derived_context(self.derived_context)
        object.__setattr__(self, "derived_context", dict(self.derived_context))

        if self.status not in {"ready", "degraded"}:
            raise ValueError("curated dossier status must be ready or degraded")
        if not isinstance(self.includes_drafts, bool) or self.includes_drafts is not request.includes_drafts:
            raise ValueError("includes_drafts must mirror request visibility")
        _validate_string_sequence("warnings", self.warnings, maximum_items=100, maximum_length=2000)
        object.__setattr__(self, "warnings", tuple(self.warnings))

        validation = self.parent_validation
        if not isinstance(validation, ValidationResult):
            raise ValueError("parent_validation must be a ValidationResult")
        if (
            validation.target_type != "dossier_revision"
            or validation.target_id != self.parent_revision_id
            or validation.status not in {"valid", "valid_with_warnings"}
            or not validation.dossier_current
            or not validation.citations_resolved
        ):
            raise ValueError("parent_validation must confirm the current parent revision")


def _validate_validation_result_identity(result: ValidationResult) -> None:
    if result.schema_version != "1.0" or result.artifact_type != "validation_result":
        raise ValueError("unsupported validation contract")
    if result.target_type not in {"dossier_revision", "writing_handoff", "writing_output", "imported_writing"}:
        raise ValueError("unsupported validation target_type")
    _bounded_string("target_id", result.target_id, minimum=1, maximum=500)
    if not isinstance(result.target_digest, str) or not _SHA256_RE.fullmatch(result.target_digest):
        raise ValueError("invalid validation target_digest")
    if result.status not in {"valid", "valid_with_warnings", "invalid"}:
        raise ValueError("unsupported validation status")


def _validate_validation_claim_types(result: ValidationResult) -> None:
    for name in (
        "schema_valid",
        "package_integrity",
        "dossier_current",
        "citations_resolved",
        "coverage_complete",
        "human_reviewed",
    ):
        if not isinstance(getattr(result, name), bool):
            raise ValueError(f"{name} must be boolean")
    if result.human_reviewed:
        raise ValueError("automatic validation cannot set human_reviewed")


def _normalize_validation_citations(
    citations: Sequence[Mapping[str, Any]],
    *,
    citations_resolved: bool,
) -> tuple[JsonObject, ...]:
    normalized = tuple(_validate_citation_result(row) for row in citations)
    if len(normalized) > 250:
        raise ValueError("validation citations exceeds 250 items")
    resolved = all(row["status"] == "valid" for row in normalized)
    if citations_resolved is not resolved:
        raise ValueError("citations_resolved does not match per-citation states")
    return normalized


def _validate_validation_result_status(result: ValidationResult) -> None:
    claims_valid = all(
        (
            result.schema_valid,
            result.package_integrity,
            result.dossier_current,
            result.citations_resolved,
            result.coverage_complete,
        )
    )
    if result.status == "valid" and (not claims_valid or result.warnings or result.errors):
        raise ValueError("valid status requires all claims and no warnings or errors")
    if result.status == "valid_with_warnings" and (not claims_valid or not result.warnings or result.errors):
        raise ValueError("valid_with_warnings requires valid claims, warnings and no errors")
    if result.status == "invalid" and not result.errors:
        raise ValueError("invalid status requires at least one error")


_CURRENT_CITATION_FIELDS = {
    "citation_id",
    "document",
    "chunk",
    "document_edge",
    "raw_edge",
    "raw_snapshot",
    "source_edge",
}


def revalidate_dossier_citations(
    repository: KnowledgeRepository,
    revision: DossierRevision,
) -> tuple[JsonObject, ...]:
    """Resolve the selected evidence against current allowlisted corpus state."""

    citations = _selected_revision_citations(revision)
    return _revalidate_revision_citations(repository, revision, citations)


def _revalidate_revision_citations(
    repository: KnowledgeRepository,
    revision: DossierRevision,
    citations: Sequence[Citation],
) -> tuple[JsonObject, ...]:
    refs = tuple(_citation_validation_ref(citation) for citation in citations)
    try:
        current_rows = hydrate_current_citations(repository, refs)
    except (ArangoError, ResearchRetrievalError, TypeError, ValueError):
        raise DossierValidationError("current citation hydration failed") from None

    try:
        return _classify_current_citations(revision, citations, current_rows)
    except DossierValidationError:
        raise
    except (KeyError, TypeError, ValueError):
        raise DossierValidationError("current citation projection failed") from None


def validate_dossier_revision(
    repository: KnowledgeRepository,
    revision: DossierRevision,
    *,
    validated_at: str,
) -> ValidationResult:
    """Validate one already-loaded dossier without repairing corpus or package."""

    citations = revalidate_dossier_citations(repository, revision)
    citations_resolved = all(row["status"] == "valid" for row in citations)
    warnings = tuple(revision.warnings)
    errors = tuple(
        f"citation {row['citation_id']} is {row['status']}: {row['reason']}" for row in citations if row["status"] != "valid"
    )
    if errors:
        status = "invalid"
    elif warnings:
        status = "valid_with_warnings"
    else:
        status = "valid"
    return ValidationResult(
        schema_version="1.0",
        artifact_type="validation_result",
        target_type="dossier_revision",
        target_id=revision.revision_id,
        target_digest=revision.content_digest,
        status=status,
        schema_valid=True,
        package_integrity=True,
        dossier_current=citations_resolved,
        citations_resolved=citations_resolved,
        coverage_complete=True,
        human_reviewed=False,
        citations=citations,
        warnings=warnings,
        errors=errors,
        validated_at=validated_at,
    )


def curate_dossier_revision(
    repository: KnowledgeRepository,
    parent: DossierRevision,
    operations: Sequence[CurationOperation],
    *,
    validated_at: str,
) -> DossierCurationResult:
    """Apply bounded owner curation to a current immutable dossier revision."""

    effective_operations = _normalize_requested_curation_operations(operations)
    _validate_requested_operation_order(effective_operations)

    try:
        parent_validation = validate_dossier_revision(repository, parent, validated_at=validated_at)
    except DossierValidationError:
        raise DossierCurationError(
            "parent current validation is unavailable",
            code="validation_unavailable",
        ) from None
    if parent_validation.status == "invalid":
        raise DossierCurationError(
            "parent revision is not current",
            code="parent_not_current",
            parent_validation=parent_validation,
        )

    candidates = cast(tuple[EvidenceCandidate, ...], tuple(parent.candidate_evidence))
    candidates_by_id = {
        candidate.citation.citation_id: candidate for candidate in candidates if isinstance(candidate.citation, Citation)
    }
    if len(candidates_by_id) != len(candidates):  # pragma: no cover - loaded manifests enforce citation shape
        raise DossierCurationError("parent candidate universe is invalid", code="invalid_parent")
    _validate_parent_selection_state(parent, candidates)

    curated_by_id = dict(candidates_by_id)
    include_citations: list[Citation] = []
    for operation in effective_operations:
        candidate = curated_by_id.get(operation.citation_id)
        if candidate is None:
            raise DossierCurationError(
                "curation operation references an unknown citation",
                code="unknown_citation",
            )
        next_state = _curation_transition(candidate.selection_state, operation.operation)
        if next_state is None:
            raise DossierCurationError(
                "curation operation is not valid for the current evidence state",
                code="invalid_transition",
            )
        curated_by_id[operation.citation_id] = _curated_candidate(
            candidate,
            selection_state=next_state,
            selection_reason=f"owner-{operation.operation}",
        )
        if operation.operation == "include":
            citation = candidate.citation
            if not isinstance(citation, Citation):  # pragma: no cover - normalized by EvidenceCandidate
                raise DossierCurationError("parent citation projection is invalid", code="invalid_parent")
            include_citations.append(citation)

    curated_candidates = tuple(
        curated_by_id[candidate.citation.citation_id] for candidate in candidates if isinstance(candidate.citation, Citation)
    )
    selected_ids = _curated_selected_ids(curated_candidates)
    if not selected_ids:
        raise DossierCurationError("curation cannot remove all selected evidence", code="empty_selection")
    if len(selected_ids) > 100:
        raise DossierCurationError("curation exceeds the selected evidence limit", code="selection_limit_exceeded")

    if include_citations:
        try:
            include_states = _revalidate_revision_citations(repository, parent, include_citations)
        except DossierValidationError:
            raise DossierCurationError(
                "include target current validation is unavailable",
                code="validation_unavailable",
            ) from None
        stale_include = next((state for state in include_states if state["status"] != "valid"), None)
        if stale_include is not None:
            raise DossierCurationError(
                f"include target is not current: {stale_include['status']}",
                code="include_not_current",
            )

    return DossierCurationResult(
        parent_revision_id=parent.revision_id,
        request=parent.request,
        corpus_context=parent.corpus_context,
        candidate_evidence=curated_candidates,
        selected_citation_ids=selected_ids,
        curation_operations=effective_operations,
        derived_context=parent.derived_context,
        status=parent.status,
        includes_drafts=parent.includes_drafts,
        warnings=parent.warnings,
        parent_validation=parent_validation,
    )


def _normalize_requested_curation_operations(
    operations: Sequence[CurationOperation],
) -> tuple[CurationOperation, ...]:
    if any(not isinstance(operation, CurationOperation) for operation in operations):
        raise DossierCurationError("curation operations are invalid", code="invalid_operation") from None
    normalized = tuple(operations)
    if not normalized:
        raise DossierCurationError("curation requires at least one operation", code="empty_operations")
    return normalized


def _validate_requested_operation_order(operations: Sequence[CurationOperation]) -> None:
    if any(operation.ordinal != ordinal for ordinal, operation in enumerate(operations)):
        raise DossierCurationError(
            "curation operation ordinals must be contiguous and ordered from zero",
            code="invalid_operation_order",
        )

    seen: dict[str, str] = {}
    for operation in operations:
        previous = seen.get(operation.citation_id)
        if previous is None:
            seen[operation.citation_id] = operation.operation
            continue
        if previous != operation.operation:
            raise DossierCurationError(
                "conflicting operations for one citation are not allowed",
                code="conflicting_operation",
            )
        raise DossierCurationError(
            "duplicate operations for one citation are not allowed",
            code="duplicate_operation",
        )


def _validate_parent_selection_state(
    parent: DossierRevision,
    candidates: Sequence[EvidenceCandidate],
) -> None:
    expected_ids = _curated_selected_ids(candidates)
    if set(parent.selected_citation_ids) != set(expected_ids):
        raise DossierCurationError("parent selected evidence state is inconsistent", code="invalid_parent")


def _curation_transition(selection_state: str, operation: str) -> str | None:
    allowed = {
        ("candidate", "include"): "selected",
        ("excluded", "include"): "selected",
        ("selected", "exclude"): "excluded",
        ("pinned", "exclude"): "excluded",
        ("selected", "pin"): "pinned",
    }
    return allowed.get((selection_state, operation))


def _curated_candidate(
    candidate: EvidenceCandidate,
    *,
    selection_state: str,
    selection_reason: str,
) -> EvidenceCandidate:
    return EvidenceCandidate(
        citation=candidate.citation,
        document_rank=candidate.document_rank,
        fragment_rank=candidate.fragment_rank,
        score=candidate.score,
        score_components=candidate.score_components,
        selection_state=selection_state,
        selection_reason=selection_reason,
    )


def _curated_selected_ids(candidates: Sequence[EvidenceCandidate]) -> tuple[str, ...]:
    return tuple(
        candidate.citation.citation_id
        for state in ("pinned", "selected")
        for candidate in candidates
        if candidate.selection_state == state and isinstance(candidate.citation, Citation)
    )


def _selected_revision_citations(revision: DossierRevision) -> tuple[Citation, ...]:
    by_id = {
        candidate.citation.citation_id: candidate.citation
        for candidate in revision.candidate_evidence
        if isinstance(candidate, EvidenceCandidate) and isinstance(candidate.citation, Citation)
    }
    try:
        return tuple(by_id[citation_id] for citation_id in revision.selected_citation_ids)
    except KeyError:  # pragma: no cover - enforced by DossierRevision
        raise DossierValidationError("selected citation projection is inconsistent") from None


def _citation_validation_ref(citation: Citation) -> JsonObject:
    return {
        "citation_id": citation.citation_id,
        "source_key": citation.source_key,
        "document_key": citation.document_key,
        "chunk_key": citation.chunk_key,
        "raw_snapshot_key": citation.raw_snapshot_key,
        "import_run_key": citation.import_run_key,
    }


def _classify_current_citations(
    revision: DossierRevision,
    citations: Sequence[Citation],
    current_rows: Any,
) -> tuple[JsonObject, ...]:
    if not isinstance(current_rows, Sequence) or isinstance(current_rows, (str, bytes)):
        raise DossierValidationError("current citation hydration returned an invalid envelope")
    if len(current_rows) != len(citations):
        raise DossierValidationError("current citation hydration returned an incomplete envelope")

    states: list[JsonObject] = []
    for citation, row in zip(citations, current_rows, strict=True):
        if not isinstance(row, Mapping) or set(row) != _CURRENT_CITATION_FIELDS:
            raise DossierValidationError("current citation hydration returned an invalid row")
        if row["citation_id"] != citation.citation_id:
            raise DossierValidationError("current citation hydration returned citations out of order")
        states.append(_classify_current_citation(revision, citation, row))
    return tuple(states)


def _classify_current_citation(
    revision: DossierRevision,
    citation: Citation,
    row: Mapping[str, Any],
) -> JsonObject:
    document = row["document"]
    if document is None:
        return _citation_state(citation, "missing", "current document is missing")
    if not isinstance(document, Mapping):
        raise DossierValidationError("current citation document has an invalid projection")

    current_status = document.get("status")
    if not isinstance(current_status, str) or not current_status:
        raise DossierValidationError("current citation document status has an invalid projection")
    request = revision.request
    if not isinstance(request, ResearchRequest):  # pragma: no cover - normalized by DossierRevision
        raise DossierValidationError("saved research request has an invalid projection")
    if current_status in {"published", "draft"} and current_status not in request.document_statuses:
        return _citation_state(citation, "hidden", "current document is outside saved visibility scope")
    if current_status not in {"published", "draft"}:
        return _citation_state(citation, "changed", "current document status is unsupported")

    chunk = row["chunk"]
    if chunk is None:
        return _citation_state(citation, "missing", "current chunk is missing")
    if not isinstance(chunk, Mapping):
        raise DossierValidationError("current citation chunk has an invalid projection")

    mismatch = _current_citation_mismatch(citation, row, document=document, chunk=chunk)
    if mismatch is not None:
        return _citation_state(citation, "changed", mismatch)
    return _citation_state(citation, "valid", None)


def _current_citation_mismatch(
    citation: Citation,
    row: Mapping[str, Any],
    *,
    document: Mapping[str, Any],
    chunk: Mapping[str, Any],
) -> str | None:
    document_mismatch = _document_citation_mismatch(citation, document)
    if document_mismatch is not None:
        return document_mismatch
    chunk_mismatch = _chunk_citation_mismatch(citation, row, chunk)
    if chunk_mismatch is not None:
        return chunk_mismatch
    excerpt_mismatch = _excerpt_citation_mismatch(citation, document, chunk)
    if excerpt_mismatch is not None:
        return excerpt_mismatch
    return _provenance_citation_mismatch(citation, row, document)


def _document_citation_mismatch(citation: Citation, document: Mapping[str, Any]) -> str | None:
    expected_document_id = f"documents/{citation.document_key}"
    checks = (
        (document.get("_key"), citation.document_key, "current document key changed"),
        (document.get("_id"), expected_document_id, "current document identity changed"),
        (document.get("source_key"), citation.source_key, "current document source changed"),
        (document.get("canonical_id"), citation.canonical_id, "current canonical identity changed"),
        (document.get("title"), citation.title, "current document title changed"),
        (document.get("published_at"), citation.published_at, "current publication time changed"),
        (document.get("status"), citation.document_status, "current document status changed"),
    )
    return _first_mismatch(checks)


def _chunk_citation_mismatch(
    citation: Citation,
    row: Mapping[str, Any],
    chunk: Mapping[str, Any],
) -> str | None:
    expected_chunk_id = f"chunks/{citation.chunk_key}"
    expected_document_id = f"documents/{citation.document_key}"
    checks = (
        (chunk.get("_key"), citation.chunk_key, "current chunk key changed"),
        (chunk.get("_id"), expected_chunk_id, "current chunk identity changed"),
        (chunk.get("document_key"), citation.document_key, "current chunk ownership changed"),
        (chunk.get("ordinal"), citation.chunk_ordinal, "current chunk ordinal changed"),
        (chunk.get("char_start"), citation.char_start, "current chunk start offset changed"),
        (chunk.get("char_end"), citation.char_end, "current chunk end offset changed"),
    )
    mismatch = _first_mismatch(checks)
    if mismatch is not None:
        return mismatch

    document_edge = row["document_edge"]
    if document_edge is None:
        return "current chunk ownership edge is missing"
    if not isinstance(document_edge, Mapping):
        raise DossierValidationError("current chunk ownership edge has an invalid projection")
    edge_checks = (
        (document_edge.get("_from"), expected_chunk_id, "current chunk ownership edge source changed"),
        (document_edge.get("_to"), expected_document_id, "current chunk ownership edge target changed"),
        (document_edge.get("ordinal"), citation.chunk_ordinal, "current chunk ownership edge ordinal changed"),
    )
    return _first_mismatch(edge_checks)


def _excerpt_citation_mismatch(
    citation: Citation,
    document: Mapping[str, Any],
    chunk: Mapping[str, Any],
) -> str | None:
    document_text = document.get("text")
    if not isinstance(document_text, str):
        return "current document text changed"
    chunk_text = chunk.get("text")
    if not isinstance(chunk_text, str):
        return "current chunk excerpt changed"

    normalized_document = " ".join(document_text.split())
    if citation.char_end > len(normalized_document):
        return "current normalized offsets no longer resolve"
    if normalized_document[citation.char_start : citation.char_end] != citation.excerpt:
        return "current normalized document slice changed"
    if chunk_text != citation.excerpt:
        return "current chunk excerpt changed"
    if hashlib.sha256(chunk_text.encode("utf-8")).hexdigest() != citation.excerpt_sha256:
        return "current chunk excerpt digest changed"
    return None


def _provenance_citation_mismatch(
    citation: Citation,
    row: Mapping[str, Any],
    document: Mapping[str, Any],
) -> str | None:
    raw_edge = _current_optional_mapping(row, "raw_edge")
    raw_snapshot = _current_optional_mapping(row, "raw_snapshot")
    source_edge = _current_optional_mapping(row, "source_edge")
    if raw_edge is None or raw_snapshot is None or source_edge is None:
        return "current citation provenance is missing"

    provenance = source_edge.get("provenance")
    if not isinstance(provenance, Mapping):
        return "current source provenance is missing"
    expected_chunk_id = f"chunks/{citation.chunk_key}"
    expected_document_id = f"documents/{citation.document_key}"
    expected_source_id = f"sources/{citation.source_key}"
    expected_raw_id = f"raw_snapshots/{citation.raw_snapshot_key}" if citation.raw_snapshot_key is not None else None
    projected_url = safe_http_url(document.get("url")) or safe_http_url(provenance.get("url"))
    raw_import = raw_edge.get("import_run_key")
    projected_import = raw_import if isinstance(raw_import, str) and raw_import else source_edge.get("import_run_key")
    raw_capture = raw_snapshot.get("captured_at")
    projected_capture = raw_capture if isinstance(raw_capture, str) and raw_capture else provenance.get("captured_at")
    checks = (
        (raw_edge.get("_from"), expected_chunk_id, "current raw provenance source changed"),
        (raw_edge.get("_to"), expected_raw_id, "current raw provenance target changed"),
        (raw_edge.get("document_key"), citation.document_key, "current raw provenance ownership changed"),
        (raw_edge.get("char_start"), citation.char_start, "current raw provenance start changed"),
        (raw_edge.get("char_end"), citation.char_end, "current raw provenance end changed"),
        (raw_snapshot.get("_key"), citation.raw_snapshot_key, "current raw snapshot key changed"),
        (raw_snapshot.get("_id"), expected_raw_id, "current raw snapshot identity changed"),
        (raw_snapshot.get("source_key"), citation.source_key, "current raw snapshot source changed"),
        (source_edge.get("_from"), expected_document_id, "current source provenance owner changed"),
        (source_edge.get("_to"), expected_source_id, "current source provenance target changed"),
        (
            provenance.get("raw_snapshot_key"),
            citation.raw_snapshot_key,
            "current source provenance snapshot changed",
        ),
        (projected_import, citation.import_run_key, "current provenance import run changed"),
        (projected_capture, citation.captured_at, "current provenance capture time changed"),
        (projected_url, citation.url, "current provenance URL changed"),
    )
    return _first_mismatch(checks)


def _current_optional_mapping(row: Mapping[str, Any], field: str) -> Mapping[str, Any] | None:
    value = row[field]
    if value is None or isinstance(value, Mapping):
        return value
    raise DossierValidationError(f"current citation {field} has an invalid projection")


def _first_mismatch(checks: Iterable[tuple[Any, Any, str]]) -> str | None:
    for current, expected, reason in checks:
        if current != expected:
            return reason
    return None


def _citation_state(citation: Citation, status: str, reason: str | None) -> JsonObject:
    return {"citation_id": citation.citation_id, "status": status, "reason": reason}


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
    if not isinstance(value, str) or not _UTC_TIMESTAMP_RE.fullmatch(value):
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
    if safe_http_url(value) != value:
        raise ValueError("url must be a canonical credential-free HTTP(S) URL or null")


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
    return canonical_sha256(value)
