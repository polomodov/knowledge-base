from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from typing import Any

import pytest

JsonObject = dict[str, Any]

_BUILT_AT = "2026-07-12T12:00:00Z"
_REVISION_ID = "rev-20260712T120000Z-01234567"
_SYNTHETIC_EXCERPT = "Synthetic evidence excerpt for contract tests."


@dataclass(frozen=True)
class DossierPackageFixture:
    path: Path
    manifest: JsonObject
    validation: JsonObject
    markdown: str

    @property
    def manifest_path(self) -> Path:
        return self.path / "manifest.json"

    @property
    def dossier_path(self) -> Path:
        return self.path / "dossier.md"

    @property
    def validation_path(self) -> Path:
        return self.path / "validation.json"


def build_research_request(**overrides: Any) -> JsonObject:
    request: JsonObject = {
        "query": "synthetic systems research",
        "source_key": None,
        "published_from": None,
        "published_to": None,
        "visibility": "published_only",
        "document_limit": 12,
        "fragments_per_document": 2,
        "evidence_limit": 24,
        "candidate_limit": 36,
        "retrieval": {
            "mode": "hybrid-chunk-v1",
            "lexical_weight": 1.0,
            "vector_weight": 1.0,
            "tie_policy": "score-desc-citation-id-asc",
        },
    }
    request.update(deepcopy(overrides))
    return request


def build_citation(**overrides: Any) -> JsonObject:
    overrides = deepcopy(overrides)
    explicit_excerpt_digest = overrides.pop("excerpt_sha256", None)
    explicit_identity_digest = overrides.pop("identity_sha256", None)
    explicit_citation_id = overrides.pop("citation_id", None)

    citation: JsonObject = {
        "projection_version": "citation-v1",
        "source_key": "synthetic-source",
        "canonical_id": "synthetic-document-1",
        "document_key": "doc-synthetic-document-1-0123456789ab",
        "chunk_key": "chunk-synthetic-document-1-0-0123456789ab",
        "chunk_ordinal": 0,
        "char_start": 0,
        "char_end": len(_SYNTHETIC_EXCERPT),
        "offset_basis": "normalized_whitespace_v1",
        "excerpt": _SYNTHETIC_EXCERPT,
        "title": "Synthetic document",
        "published_at": "2026-01-15T10:00:00Z",
        "document_status": "published",
        "url": "https://example.test/synthetic-document-1",
        "raw_snapshot_key": "raw-synthetic-0123456789ab",
        "import_run_key": "import-synthetic-0123456789ab",
        "captured_at": "2026-01-15T10:05:00Z",
    }
    citation.update(overrides)

    excerpt_digest = explicit_excerpt_digest or _sha256_text(str(citation["excerpt"]))
    citation["excerpt_sha256"] = excerpt_digest
    identity_projection = {
        "projection_version": citation["projection_version"],
        "source_key": citation["source_key"],
        "canonical_id": citation["canonical_id"],
        "document_key": citation["document_key"],
        "chunk_key": citation["chunk_key"],
        "char_start": citation["char_start"],
        "char_end": citation["char_end"],
        "offset_basis": citation["offset_basis"],
        "excerpt_sha256": excerpt_digest,
    }
    identity_digest = explicit_identity_digest or _sha256_json(identity_projection)
    citation["identity_sha256"] = identity_digest
    citation["citation_id"] = explicit_citation_id or f"cit-{identity_digest[:16]}"
    return citation


def build_evidence_candidate(*, citation: JsonObject | None = None, **overrides: Any) -> JsonObject:
    candidate: JsonObject = {
        "citation": deepcopy(citation) if citation is not None else build_citation(),
        "document_rank": 1,
        "fragment_rank": 1,
        "score": 1.0,
        "score_components": {"lexical": 1.0, "vector": 1.0, "graph_lead": None},
        "selection_state": "selected",
        "selection_reason": "automatic-round-1",
    }
    candidate.update(deepcopy(overrides))
    return candidate


def build_dossier_manifest(
    *,
    request: JsonObject | None = None,
    candidate_evidence: list[JsonObject] | None = None,
    corpus_context: JsonObject | None = None,
    **overrides: Any,
) -> JsonObject:
    effective_request = deepcopy(request) if request is not None else build_research_request()
    candidates = deepcopy(candidate_evidence) if candidate_evidence is not None else [build_evidence_candidate()]
    selected_ids = [
        candidate["citation"]["citation_id"]
        for candidate in candidates
        if candidate.get("selection_state") in {"selected", "pinned"}
    ]
    if not selected_ids and candidates:
        selected_ids = [candidates[0]["citation"]["citation_id"]]

    context = deepcopy(corpus_context) if corpus_context is not None else _build_corpus_context()
    manifest: JsonObject = {
        "schema_version": "1.0",
        "artifact_type": "dossier_revision",
        "dossier_key": _dossier_key(effective_request),
        "revision_id": _REVISION_ID,
        "parent_revision_id": None,
        "request": effective_request,
        "corpus_context": context,
        "candidate_evidence": candidates,
        "selected_citation_ids": selected_ids,
        "curation_operations": [],
        "derived_context": {"topics": [], "leads": []},
        "status": "ready",
        "includes_drafts": effective_request["visibility"] == "published_and_drafts",
        "warnings": [],
        "files": {
            "dossier": _file_digest("dossier.md", b""),
            "validation": _file_digest("validation.json", b""),
        },
    }
    manifest.update(deepcopy(overrides))
    if "content_digest" not in overrides:
        manifest["content_digest"] = _sha256_json(_dossier_content_projection(manifest))
    return manifest


def build_dossier_package(
    output_root: Path,
    *,
    manifest: JsonObject | None = None,
    markdown: str | None = None,
    validation: JsonObject | None = None,
) -> DossierPackageFixture:
    package_manifest = deepcopy(manifest) if manifest is not None else build_dossier_manifest()
    package_markdown = markdown if markdown is not None else _dossier_markdown(package_manifest)
    package_validation = deepcopy(validation) if validation is not None else _dossier_validation(package_manifest)

    markdown_bytes = package_markdown.encode("utf-8")
    validation_bytes = _pretty_json_bytes(package_validation)
    package_manifest["files"] = {
        "dossier": _file_digest("dossier.md", markdown_bytes),
        "validation": _file_digest("validation.json", validation_bytes),
    }

    package_path = output_root / package_manifest["dossier_key"] / "revisions" / package_manifest["revision_id"]
    package_path.mkdir(parents=True)
    (package_path / "dossier.md").write_bytes(markdown_bytes)
    (package_path / "validation.json").write_bytes(validation_bytes)
    (package_path / "manifest.json").write_bytes(_pretty_json_bytes(package_manifest))
    return DossierPackageFixture(
        path=package_path,
        manifest=package_manifest,
        validation=package_validation,
        markdown=package_markdown,
    )


@pytest.fixture
def research_request_builder() -> Callable[..., JsonObject]:
    return build_research_request


@pytest.fixture
def citation_builder() -> Callable[..., JsonObject]:
    return build_citation


@pytest.fixture
def evidence_candidate_builder() -> Callable[..., JsonObject]:
    return build_evidence_candidate


@pytest.fixture
def dossier_manifest_builder() -> Callable[..., JsonObject]:
    return build_dossier_manifest


@pytest.fixture
def dossier_package_builder(tmp_path: Path) -> Callable[..., DossierPackageFixture]:
    sequence = count(1)

    def build(**kwargs: Any) -> DossierPackageFixture:
        output_root = kwargs.pop("output_root", tmp_path / f"research-{next(sequence)}")
        return build_dossier_package(output_root, **kwargs)

    return build


def _build_corpus_context() -> JsonObject:
    return {
        "database": "knowledge_base_test",
        "built_at": _BUILT_AT,
        "embedding_model": "hash-v1",
        "embedding_dimension": 8,
        "retrieval_min_similarity": 0.0,
        "latest_import_run_key": None,
        "latest_index_runs": {},
        "git_revision": None,
        "warnings": [],
    }


def _dossier_key(request: JsonObject) -> str:
    normalized = str(request["query"]).strip().lower()
    slug = re.sub(r"[^a-z0-9_-]+", "-", normalized).strip("-_") or "topic"
    return f"research-{slug[:64]}-{_sha256_json(request)[:12]}"


def _dossier_content_projection(manifest: JsonObject) -> JsonObject:
    projection = deepcopy(manifest)
    for field in ("content_digest", "revision_id", "parent_revision_id", "files"):
        projection.pop(field, None)
    corpus_context = projection.get("corpus_context")
    if isinstance(corpus_context, dict):
        corpus_context.pop("built_at", None)
        corpus_context.pop("latest_import_run_key", None)
        corpus_context.pop("latest_index_runs", None)
    for candidate in projection.get("candidate_evidence", []):
        citation = candidate.get("citation") if isinstance(candidate, dict) else None
        if isinstance(citation, dict):
            citation.pop("import_run_key", None)
    return projection


def _dossier_markdown(manifest: JsonObject) -> str:
    excerpts = {
        candidate["citation"]["citation_id"]: candidate["citation"]["excerpt"] for candidate in manifest["candidate_evidence"]
    }
    lines = [
        "# Synthetic research dossier",
        "",
        f"Query: {manifest['request']['query']}",
        "",
    ]
    for citation_id in manifest["selected_citation_ids"]:
        lines.extend((f"## {citation_id}", "", str(excerpts[citation_id]), ""))
    return "\n".join(lines)


def _dossier_validation(manifest: JsonObject) -> JsonObject:
    citations = [
        {"citation_id": citation_id, "status": "valid", "reason": None} for citation_id in manifest["selected_citation_ids"]
    ]
    return {
        "schema_version": "1.0",
        "artifact_type": "validation_result",
        "target_type": "dossier_revision",
        "target_id": manifest["revision_id"],
        "target_digest": manifest["content_digest"],
        "status": "valid",
        "schema_valid": True,
        "package_integrity": True,
        "dossier_current": True,
        "citations_resolved": True,
        "coverage_complete": True,
        "human_reviewed": False,
        "citations": citations,
        "warnings": [],
        "errors": [],
        "validated_at": _BUILT_AT,
    }


def _file_digest(path: str, payload: bytes) -> JsonObject:
    return {"path": path, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _pretty_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
