from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import pytest

from knowledge_base.arango import ArangoClient, ArangoError
from knowledge_base.cli.main import main as cli_main
from knowledge_base.config import Settings, load_settings
from knowledge_base.constants import DOCUMENT_COLLECTIONS, EDGE_COLLECTIONS
from knowledge_base.embeddings import hash_embedding
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.research_artifacts import canonical_json_bytes
from knowledge_base.research_retrieval import lexical_chunk_candidates
from knowledge_base.research_workflow import ResearchRequest
from knowledge_base.schema import bootstrap_schema

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("KB_RUN_INTEGRATION") != "1", reason="set KB_RUN_INTEGRATION=1 with ArangoDB running"),
]

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures/research/safe-research-corpus.json"
QUERY = "grounded graph context evidence telescope notes"
COLLECTIONS = tuple(sorted([*DOCUMENT_COLLECTIONS, *EDGE_COLLECTIONS]))


@dataclass(frozen=True)
class SeededResearchCorpus:
    settings: Settings
    repository: KnowledgeRepository
    fixture: dict[str, object]


@pytest.fixture
def seeded_research_corpus(monkeypatch: pytest.MonkeyPatch) -> Iterator[SeededResearchCorpus]:
    base = load_settings()
    database = f"kb_research_{uuid4().hex[:16]}"
    settings = replace(
        base,
        arango_database=database,
        embedding_provider="hash",
        embedding_dimension=8,
        retrieval_min_similarity=0.0,
    )
    client = ArangoClient(settings)
    fixture = _load_fixture()

    monkeypatch.setenv("KB_ARANGO_URL", settings.arango_url)
    monkeypatch.setenv("KB_ARANGO_DATABASE", database)
    monkeypatch.setenv("KB_ARANGO_USER", settings.arango_user)
    monkeypatch.setenv("KB_ARANGO_PASSWORD", settings.arango_password)
    monkeypatch.setenv("KB_EMBEDDING_PROVIDER", "hash")
    monkeypatch.setenv("KB_EMBEDDING_DIMENSION", "8")
    monkeypatch.setenv("KB_RETRIEVAL_MIN_SIMILARITY", "0")

    try:
        client.ensure_database()
        # The first bootstrap establishes collections. The second, after strict fixture
        # seeding, is the only index build and can train the vector index over real rows.
        bootstrap_schema(client, embedding_dimension=8)
        repository = KnowledgeRepository(client)
        _seed_fixture(repository, fixture)
        bootstrap_schema(client, embedding_dimension=8)
        _wait_for_text_index(repository)
        yield SeededResearchCorpus(settings=settings, repository=repository, fixture=fixture)
    finally:
        try:
            ArangoClient(base).request(
                "DELETE",
                f"/_api/database/{quote(database)}",
                expected=(200, 202),
            )
        except ArangoError as error:
            if error.status != 404:
                raise


def test_research_build_pipeline_is_visibility_safe_reproducible_and_read_only(
    seeded_research_corpus: SeededResearchCorpus,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = seeded_research_corpus.repository
    fixture = seeded_research_corpus.fixture
    output_root = tmp_path / "research"
    before = _database_snapshot(repository)

    common = [
        "research",
        "build",
        QUERY,
        "--output-root",
        str(output_root),
        "--acknowledge-unsafe-output",
        "--documents",
        "4",
        "--fragments-per-document",
        "2",
    ]
    first = _run_cli(capsys, common, expected_exit=0)
    first_path, first_manifest, first_markdown = _read_package(first)
    first_files = _tree_snapshot(first_path)

    assert first["status"] in {"ok", "degraded"}
    assert first_manifest["includes_drafts"] is False
    assert first_manifest["request"]["visibility"] == "published_only"
    assert "output_outside_generated_zone" not in first_manifest["warnings"]
    assert "output_outside_generated_zone" not in first_manifest["corpus_context"]["warnings"]
    assert all(row["citation"]["document_status"] == "published" for row in first_manifest["candidate_evidence"])
    assert "research-draft-hidden" not in json.dumps(first_manifest, ensure_ascii=False)
    exposed = json.dumps(first_manifest, ensure_ascii=False).lower() + first_markdown.lower()
    for marker in fixture["expectations"]["forbidden_published_only_markers"]:  # type: ignore[index]
        assert str(marker).lower() not in exposed
    assert fixture["expectations"]["tainted_community_key"] not in exposed  # type: ignore[index]
    _assert_exact_provenance(first_manifest, fixture)

    repeated = _run_cli(capsys, common, expected_exit=0)
    repeated_path, repeated_manifest, _ = _read_package(repeated)
    assert repeated["dossier_key"] == first["dossier_key"]
    assert repeated["content_digest"] == first["content_digest"]
    assert repeated["revision_id"] != first["revision_id"]
    assert repeated_path != first_path
    assert _tree_snapshot(first_path) == first_files
    assert repeated_manifest["content_digest"] == first_manifest["content_digest"]

    draft = _run_cli(capsys, [*common, "--include-drafts"], expected_exit=0)
    _, draft_manifest, draft_markdown = _read_package(draft)
    assert draft_manifest["includes_drafts"] is True
    assert draft_manifest["request"]["visibility"] == "published_and_drafts"
    assert "draft_visibility_enabled" in draft_manifest["warnings"]
    assert "draft_visibility_enabled" in draft_markdown
    assert any(
        row["selection_state"] in {"selected", "pinned"} and row["citation"]["document_status"] == "draft"
        for row in draft_manifest["candidate_evidence"]
    )

    output_before_no_evidence = _tree_snapshot(output_root)
    no_evidence = _run_cli(
        capsys,
        [*common, "--source", "research-source-that-does-not-exist"],
        expected_exit=1,
    )
    assert no_evidence["status"] == "no_evidence"
    assert _tree_snapshot(output_root) == output_before_no_evidence
    assert _database_snapshot(repository) == before


def test_research_validate_and_curate_publish_an_immutable_child_without_database_mutations(
    seeded_research_corpus: SeededResearchCorpus,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = seeded_research_corpus.repository
    output_root = tmp_path / "research"
    database_before = _database_snapshot(repository)
    parent = _run_cli(
        capsys,
        [
            "research",
            "build",
            QUERY,
            "--output-root",
            str(output_root),
            "--acknowledge-unsafe-output",
            "--documents",
            "2",
            "--fragments-per-document",
            "1",
        ],
        expected_exit=0,
    )
    parent_path, parent_manifest, _ = _read_package(parent)
    parent_files = _tree_snapshot(parent_path)
    artifact_tree_before_validation = _tree_snapshot(output_root)

    validation = _run_cli(
        capsys,
        ["research", "validate", str(parent_path), "--output-root", str(output_root)],
        expected_exit=0,
    )

    assert validation["artifact_type"] == "validation_result"
    assert validation["target_type"] == "dossier_revision"
    assert validation["target_id"] == parent_manifest["revision_id"]
    assert validation["target_digest"] == parent_manifest["content_digest"]
    assert validation["status"] in {"valid", "valid_with_warnings"}
    assert validation["dossier_current"] is validation["citations_resolved"] is True
    assert {row["status"] for row in validation["citations"]} == {"valid"}  # type: ignore[union-attr]
    assert _tree_snapshot(output_root) == artifact_tree_before_validation
    assert _database_snapshot(repository) == database_before

    selected = [
        row
        for row in parent_manifest["candidate_evidence"]
        if row["selection_state"] == "selected"  # type: ignore[union-attr]
    ]
    candidates = [
        row
        for row in parent_manifest["candidate_evidence"]
        if row["selection_state"] == "candidate"  # type: ignore[union-attr]
    ]
    assert len(selected) >= 2 and candidates
    include_id = candidates[0]["citation"]["citation_id"]
    exclude_id = selected[0]["citation"]["citation_id"]
    pin_id = selected[1]["citation"]["citation_id"]
    reason = "synthetic owner curation"

    child = _run_cli(
        capsys,
        [
            "research",
            "curate",
            str(parent_path),
            "--include",
            include_id,
            "--exclude",
            exclude_id,
            "--pin",
            pin_id,
            "--reason",
            reason,
            "--output-root",
            str(output_root),
            "--acknowledge-unsafe-output",
        ],
        expected_exit=0,
    )
    child_path, child_manifest, _ = _read_package(child)

    assert child["status"] == "ok"
    assert child["parent_revision_id"] == parent_manifest["revision_id"]
    assert child["operations"] == 3
    assert child_path.parent == parent_path.parent and child_path != parent_path
    assert child_manifest["parent_revision_id"] == parent_manifest["revision_id"]
    assert child_manifest["content_digest"] != parent_manifest["content_digest"]
    assert child_manifest["curation_operations"] == [
        {"operation": "include", "citation_id": include_id, "reason": reason, "ordinal": 0},
        {"operation": "exclude", "citation_id": exclude_id, "reason": reason, "ordinal": 1},
        {"operation": "pin", "citation_id": pin_id, "reason": reason, "ordinal": 2},
    ]
    parent_ids = [row["citation"]["citation_id"] for row in parent_manifest["candidate_evidence"]]  # type: ignore[union-attr]
    child_ids = [row["citation"]["citation_id"] for row in child_manifest["candidate_evidence"]]  # type: ignore[union-attr]
    child_states = {
        row["citation"]["citation_id"]: row["selection_state"]
        for row in child_manifest["candidate_evidence"]  # type: ignore[union-attr]
    }
    assert child_ids == parent_ids
    assert child_states[include_id] == "selected"
    assert child_states[exclude_id] == "excluded"
    assert child_states[pin_id] == "pinned"
    assert _tree_snapshot(parent_path) == parent_files
    assert _database_snapshot(repository) == database_before

    child_tree_before_validation = _tree_snapshot(output_root)
    child_validation = _run_cli(
        capsys,
        ["research", "validate", str(child_path), "--output-root", str(output_root)],
        expected_exit=0,
    )
    assert child_validation["target_id"] == child_manifest["revision_id"]
    assert child_validation["status"] in {"valid", "valid_with_warnings"}
    assert child_validation["dossier_current"] is child_validation["citations_resolved"] is True
    assert _tree_snapshot(output_root) == child_tree_before_validation
    assert _tree_snapshot(parent_path) == parent_files
    assert _database_snapshot(repository) == database_before


@pytest.mark.parametrize("citation_state", ["missing", "changed", "hidden"])
def test_research_curate_rejects_a_non_current_parent_without_artifact_or_database_mutations(
    citation_state: str,
    seeded_research_corpus: SeededResearchCorpus,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = seeded_research_corpus.repository
    output_root = tmp_path / "research"
    database_before_build = _database_snapshot(repository)
    parent = _run_cli(
        capsys,
        [
            "research",
            "build",
            QUERY,
            "--output-root",
            str(output_root),
            "--acknowledge-unsafe-output",
            "--documents",
            "4",
            "--fragments-per-document",
            "2",
        ],
        expected_exit=0,
    )
    parent_path, parent_manifest, _ = _read_package(parent)
    parent_files = _tree_snapshot(parent_path)
    assert _database_snapshot(repository) == database_before_build
    selected = [
        row
        for row in parent_manifest["candidate_evidence"]
        if row["selection_state"] == "selected"  # type: ignore[union-attr]
    ]
    assert selected
    citation = selected[0]["citation"]
    citation_id = citation["citation_id"]
    _make_citation_non_current(
        repository,
        seeded_research_corpus.fixture,
        citation,
        citation_state,
    )
    database_before_commands = _database_snapshot(repository)
    artifacts_before_commands = _tree_snapshot(output_root)

    validation = _run_cli(
        capsys,
        ["research", "validate", str(parent_path), "--output-root", str(output_root)],
        expected_exit=1,
    )
    validation_states = {row["citation_id"]: row["status"] for row in validation["citations"]}  # type: ignore[union-attr]
    assert validation["status"] == "invalid"
    assert validation["target_id"] == parent_manifest["revision_id"]
    assert validation["dossier_current"] is validation["citations_resolved"] is False
    assert validation_states[citation_id] == citation_state
    assert _tree_snapshot(output_root) == artifacts_before_commands
    assert _database_snapshot(repository) == database_before_commands

    rejection = _run_cli(
        capsys,
        [
            "research",
            "curate",
            str(parent_path),
            "--exclude",
            citation_id,
            "--reason",
            "must reject a stale parent",
            "--output-root",
            str(output_root),
            "--acknowledge-unsafe-output",
        ],
        expected_exit=1,
    )
    rejected_validation = rejection["validation"]
    rejected_states = {
        row["citation_id"]: row["status"]
        for row in rejected_validation["citations"]  # type: ignore[union-attr]
    }
    assert rejection["status"] == "rejected"
    assert rejection["reason"] == "parent_not_current"
    assert rejected_validation["status"] == "invalid"
    assert rejected_states[citation_id] == citation_state
    assert _tree_snapshot(output_root) == artifacts_before_commands
    assert _tree_snapshot(parent_path) == parent_files
    assert _database_snapshot(repository) == database_before_commands


@pytest.mark.parametrize(
    ("output_kind", "max_words"),
    [("draft", "800"), ("summary", "250")],
)
def test_research_writing_round_trip_validates_every_artifact_and_reuses_identical_import(
    output_kind: str,
    max_words: str,
    seeded_research_corpus: SeededResearchCorpus,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    writing_output_package_builder,
) -> None:
    repository = seeded_research_corpus.repository
    database_before = _database_snapshot(repository)
    output_root = tmp_path / "research"
    dossier_path, dossier_manifest, handoff_path, handoff = _build_integration_handoff(
        capsys,
        output_root,
        output_kind=output_kind,
        max_words=max_words,
    )
    assert _database_snapshot(repository) == database_before

    for artifact, extra_arguments, target_type in (
        (dossier_path, [], "dossier_revision"),
        (handoff_path, [], "writing_handoff"),
    ):
        validation = _run_cli(
            capsys,
            [
                "research",
                "validate",
                str(artifact),
                "--output-root",
                str(output_root),
                *extra_arguments,
            ],
            expected_exit=0,
        )
        assert validation["target_type"] == target_type
        assert validation["status"] in {"valid", "valid_with_warnings"}

    writing_output = writing_output_package_builder(handoff=handoff)
    incoming_path = tmp_path / f"writing-output-{output_kind}.json"
    incoming_path.write_bytes(canonical_json_bytes(writing_output))
    incoming_validation = _run_cli(
        capsys,
        [
            "research",
            "validate",
            str(incoming_path),
            "--handoff",
            str(handoff_path),
            "--output-root",
            str(output_root),
        ],
        expected_exit=0,
    )
    assert incoming_validation["target_type"] == "writing_output"
    assert incoming_validation["status"] in {"valid", "valid_with_warnings"}

    imported = _run_cli(
        capsys,
        [
            "research",
            "import-output",
            str(incoming_path),
            "--handoff",
            str(handoff_path),
            "--output-root",
            str(output_root),
            "--acknowledge-unsafe-output",
        ],
        expected_exit=0,
    )
    imported_path = Path(str(imported["output"]))
    assert imported_path.is_dir()
    assert {entry.name for entry in imported_path.iterdir()} == {"manifest.json", "output.md", "validation.json"}
    imported_manifest = json.loads((imported_path / "manifest.json").read_text(encoding="utf-8"))
    imported_markdown = (imported_path / "output.md").read_text(encoding="utf-8")
    assert imported["status"] in {"ok", "reused"}
    assert imported["output_kind"] == imported_manifest["output_kind"] == output_kind
    assert imported["writing_id"] == imported_manifest["writing_id"]
    assert imported_manifest["incoming_package_digest"] == writing_output["package_digest"]
    assert imported_manifest["handoff_id"] == handoff["handoff_id"]
    assert imported_manifest["revision_id"] == dossier_manifest["revision_id"]
    assert imported_manifest["human_reviewed"] is False
    assert writing_output["content_markdown"] in imported_markdown

    imported_validation = _run_cli(
        capsys,
        [
            "research",
            "validate",
            str(imported_path),
            "--output-root",
            str(output_root),
        ],
        expected_exit=0,
    )
    assert imported_validation["target_type"] == "imported_writing"
    assert imported_validation["status"] in {"valid", "valid_with_warnings"}

    artifacts_before_repeat = _tree_snapshot(output_root)
    repeated = _run_cli(
        capsys,
        [
            "research",
            "import-output",
            str(incoming_path),
            "--handoff",
            str(handoff_path),
            "--output-root",
            str(output_root),
            "--acknowledge-unsafe-output",
        ],
        expected_exit=0,
    )
    assert repeated["writing_id"] == imported["writing_id"]
    assert repeated["output"] == imported["output"]
    assert _tree_snapshot(output_root) == artifacts_before_repeat
    assert _database_snapshot(repository) == database_before


@pytest.mark.parametrize("rejection", ["wrong_kind", "unknown_citation", "changed_evidence"])
def test_research_writing_import_rejects_the_whole_package_without_artifact_or_database_mutation(
    rejection: str,
    seeded_research_corpus: SeededResearchCorpus,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    writing_output_package_builder,
) -> None:
    repository = seeded_research_corpus.repository
    output_root = tmp_path / "research"
    _, dossier_manifest, handoff_path, handoff = _build_integration_handoff(
        capsys,
        output_root,
        output_kind="draft",
        max_words="800",
    )
    writing_output = writing_output_package_builder(handoff=handoff)
    if rejection == "wrong_kind":
        writing_output = writing_output_package_builder(handoff=handoff, output_kind="summary")
    elif rejection == "unknown_citation":
        sections = deepcopy(writing_output["sections"])
        sections[0]["citation_ids"] = ["cit-deadbeefdeadbeef"]
        writing_output = writing_output_package_builder(
            handoff=handoff,
            content_markdown=writing_output["content_markdown"],
            sections=sections,
        )
    else:
        citation_id = dossier_manifest["selected_citation_ids"][0]
        citation = next(
            row["citation"] for row in dossier_manifest["candidate_evidence"] if row["citation"]["citation_id"] == citation_id
        )
        _make_citation_non_current(
            repository,
            seeded_research_corpus.fixture,
            citation,
            "changed",
        )

    incoming_path = tmp_path / f"rejected-writing-output-{rejection}.json"
    incoming_path.write_bytes(canonical_json_bytes(writing_output))
    database_before_commands = _database_snapshot(repository)
    artifacts_before_commands = _tree_snapshot(output_root)

    validation = _run_cli(
        capsys,
        [
            "research",
            "validate",
            str(incoming_path),
            "--handoff",
            str(handoff_path),
            "--output-root",
            str(output_root),
        ],
        expected_exit=1,
    )
    assert validation["status"] == "invalid"
    assert _tree_snapshot(output_root) == artifacts_before_commands
    assert _database_snapshot(repository) == database_before_commands

    rejected = _run_cli(
        capsys,
        [
            "research",
            "import-output",
            str(incoming_path),
            "--handoff",
            str(handoff_path),
            "--output-root",
            str(output_root),
            "--acknowledge-unsafe-output",
        ],
        expected_exit=1,
    )
    assert rejected["status"] in {"invalid", "rejected"}
    assert _tree_snapshot(output_root) == artifacts_before_commands
    assert _database_snapshot(repository) == database_before_commands


def _build_integration_handoff(
    capsys: pytest.CaptureFixture[str],
    output_root: Path,
    *,
    output_kind: str,
    max_words: str,
) -> tuple[Path, dict[str, object], Path, dict[str, object]]:
    dossier = _run_cli(
        capsys,
        [
            "research",
            "build",
            QUERY,
            "--output-root",
            str(output_root),
            "--acknowledge-unsafe-output",
            "--documents",
            "2",
            "--fragments-per-document",
            "1",
        ],
        expected_exit=0,
    )
    dossier_path, dossier_manifest, _ = _read_package(dossier)
    handoff_result = _run_cli(
        capsys,
        [
            "research",
            "handoff",
            str(dossier_path),
            "--output-kind",
            output_kind,
            "--language",
            "ru",
            "--style",
            "synthetic integration style",
            "--max-words",
            max_words,
            "--acknowledge-external-disclosure",
            "--output-root",
            str(output_root),
            "--acknowledge-unsafe-output",
        ],
        expected_exit=0,
    )
    handoff_path = Path(str(handoff_result["output"]))
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    assert handoff_result["handoff_id"] == handoff["handoff_id"]
    assert handoff["requested_output"]["kind"] == output_kind
    assert handoff["egress_acknowledged"] is True
    assert handoff["dossier_key"] == dossier_manifest["dossier_key"]
    return dossier_path, dossier_manifest, handoff_path, handoff


def _load_fixture() -> dict[str, object]:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    expected_fields = {
        "schema_version",
        "fixture_type",
        "description",
        "embedding",
        "sources",
        "import_runs",
        "raw_snapshots",
        "documents",
        "chunks",
        "topics",
        "communities",
        "edges",
        "expectations",
    }
    assert isinstance(fixture, dict) and set(fixture) == expected_fields
    assert fixture["schema_version"] == "1.0"
    assert fixture["fixture_type"] == "safe_research_corpus"
    assert fixture["embedding"] == {"provider": "hash", "model": "hash-v1", "dimension": 8}
    for field in ("sources", "import_runs", "raw_snapshots", "documents", "chunks", "topics", "communities"):
        assert isinstance(fixture[field], list) and fixture[field]
        assert all(isinstance(row, dict) and isinstance(row.get("_key"), str) for row in fixture[field])
    edges = fixture["edges"]
    assert isinstance(edges, dict) and set(edges) == {
        "document_from_source",
        "chunk_of_document",
        "chunk_derived_from_raw",
        "document_mentions_topic",
        "item_related_to_item",
        "document_in_community",
    }
    assert all(isinstance(rows, list) and rows for rows in edges.values())
    return fixture


def _seed_fixture(repository: KnowledgeRepository, fixture: dict[str, object]) -> None:
    for field in ("sources", "raw_snapshots", "documents", "topics", "communities", "import_runs"):
        for row in fixture[field]:  # type: ignore[union-attr]
            repository.upsert(field, dict(row))
    for row in fixture["chunks"]:  # type: ignore[union-attr]
        chunk = dict(row)
        chunk["embedding"] = hash_embedding(str(chunk["text"]), dimension=8)
        chunk["embedding_model"] = "hash-v1"
        repository.upsert("chunks", chunk)
    for collection, rows in fixture["edges"].items():  # type: ignore[union-attr]
        for row in rows:
            repository.upsert_edge(collection, dict(row))


def _make_citation_non_current(
    repository: KnowledgeRepository,
    fixture: dict[str, object],
    citation: dict[str, object],
    citation_state: str,
) -> None:
    if citation_state == "missing":
        repository.client.aql(
            "REMOVE @key IN @@collection",
            {"@collection": "chunks", "key": citation["chunk_key"]},
        )
        return

    if citation_state == "changed":
        chunk = _fixture_row(fixture, "chunks", str(citation["chunk_key"]))
        chunk["text"] = f"{chunk['text']} Changed after dossier publication."
        repository.upsert("chunks", chunk)
        return

    assert citation_state == "hidden"
    document = _fixture_row(fixture, "documents", str(citation["document_key"]))
    document["status"] = "draft"
    repository.upsert("documents", document)


def _fixture_row(fixture: dict[str, object], collection: str, key: str) -> dict[str, object]:
    rows = fixture[collection]
    assert isinstance(rows, list)
    for row in rows:
        if isinstance(row, dict) and row.get("_key") == key:
            return dict(row)
    raise AssertionError(f"fixture row {collection}/{key} is missing")


def _wait_for_text_index(repository: KnowledgeRepository) -> None:
    request = ResearchRequest(query=QUERY, candidate_limit=12, evidence_limit=8)
    for _ in range(40):
        if lexical_chunk_candidates(repository, request):
            return
        time.sleep(0.25)
    raise AssertionError("research fixture did not become visible in ArangoSearch")


def _run_cli(
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    *,
    expected_exit: int,
) -> dict[str, object]:
    capsys.readouterr()
    exit_code = cli_main(argv)
    captured = capsys.readouterr()
    assert exit_code == expected_exit, captured
    payload = json.loads(captured.out)
    assert isinstance(payload, dict)
    return payload


def _read_package(payload: dict[str, object]) -> tuple[Path, dict[str, object], str]:
    path = Path(str(payload["output"]))
    assert path.is_dir()
    assert {entry.name for entry in path.iterdir()} == {"manifest.json", "dossier.md", "validation.json"}
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    markdown = (path / "dossier.md").read_text(encoding="utf-8")
    assert isinstance(manifest, dict)
    return path, manifest, markdown


def _assert_exact_provenance(manifest: dict[str, object], fixture: dict[str, object]) -> None:
    documents = {row["_key"]: row for row in fixture["documents"]}  # type: ignore[union-attr]
    chunks = {row["_key"]: row for row in fixture["chunks"]}  # type: ignore[union-attr]
    raw_snapshots = {row["_key"]: row for row in fixture["raw_snapshots"]}  # type: ignore[union-attr]
    raw_edges = {row["_from"].split("/", 1)[1]: row for row in fixture["edges"]["chunk_derived_from_raw"]}  # type: ignore[index]
    source_edges = {row["_from"].split("/", 1)[1]: row for row in fixture["edges"]["document_from_source"]}  # type: ignore[index]

    for candidate in manifest["candidate_evidence"]:  # type: ignore[union-attr]
        citation = candidate["citation"]
        chunk = chunks[citation["chunk_key"]]
        document = documents[citation["document_key"]]
        raw_edge = raw_edges[chunk["_key"]]
        raw_key = raw_edge["_to"].split("/", 1)[1]
        raw = raw_snapshots[raw_key]
        source_edge = source_edges[document["_key"]]
        normalized = " ".join(document["text"].split())

        assert citation["excerpt"] == chunk["text"] == normalized[chunk["char_start"] : chunk["char_end"]]
        assert citation["excerpt_sha256"] == hashlib.sha256(chunk["text"].encode()).hexdigest()
        assert citation["source_key"] == document["source_key"] == raw["source_key"]
        assert citation["raw_snapshot_key"] == raw_key == source_edge["provenance"]["raw_snapshot_key"]
        assert citation["import_run_key"] == raw_edge["import_run_key"] == source_edge["import_run_key"]
        assert citation["captured_at"] == raw["captured_at"]
        assert citation["url"] == document["url"]


def _database_snapshot(repository: KnowledgeRepository) -> dict[str, bytes]:
    return {
        collection: canonical_json_bytes(
            repository.client.aql(
                "FOR document IN @@collection SORT document._key ASC RETURN document",
                {"@collection": collection},
            )
        )
        for collection in COLLECTIONS
    }


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else b"<directory>" for path in sorted(root.rglob("*"))
    }
