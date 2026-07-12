from __future__ import annotations

import argparse
import os
import sys
import traceback
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Never

from knowledge_base.arango import ArangoClient, ArangoError
from knowledge_base.config import load_settings
from knowledge_base.embeddings import build_embedding_provider
from knowledge_base.exporting import export_jsonl
from knowledge_base.fixture import ingest_fixture
from knowledge_base.graph_export import export_graph
from knowledge_base.indexing import rebuild_indexes
from knowledge_base.json_output import emit_json
from knowledge_base.platform import platform_down, platform_up
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.research_artifacts import (
    load_dossier_package,
    materialize_curated_dossier_package,
    materialize_dossier_package,
    publish_dossier_package,
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
from knowledge_base.retrieval import (
    global_search,
    graph_neighbors,
    hybrid_search,
    local_search,
    semantic_search,
    text_search,
)
from knowledge_base.schema import bootstrap_schema, health_report
from knowledge_base.sources.book_cube import DEFAULT_PUBLIC_URL as BOOK_CUBE_DEFAULT_URL
from knowledge_base.sources.book_cube import ingest_book_cube, ingest_book_cube_archive
from knowledge_base.sources.medium_export import ingest_medium_export
from knowledge_base.sources.tellmeabout_tech import DEFAULT_FEED_URL, ingest_tellmeabout_tech
from knowledge_base.viz_builder import DEFAULT_VIZ_OUTPUT, build_visualization

_MIN_SIMILARITY_HELP = "Relevance floor for semantic hits (default from config)"
_RESEARCH_WARNING_MESSAGES = {
    "draft_visibility_enabled": "draft evidence visibility is enabled; exact excerpts may contain private material",
    "output_outside_generated_zone": "output root is outside data/generated; explicit acknowledgement was accepted",
}
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


class CliUsageError(ValueError):
    """Command-line syntax or command selection is invalid."""


class _CliHelpRequested(Exception):
    pass


class _CliArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise CliUsageError(message)

    def exit(self, status: int = 0, message: str | None = None) -> Never:
        if message:
            self._print_message(message)
        if status == 0:
            raise _CliHelpRequested
        raise CliUsageError(message or f"argument parser exited with status {status}")


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


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args: argparse.Namespace | None = None
    try:
        args = parser.parse_args(argv)
        if not hasattr(args, "handler"):
            raise CliUsageError("a complete command is required")
        return args.handler(args)
    except _CliHelpRequested:
        return 0
    except ArangoError as error:
        warnings = _error_warnings(args)
        _emit_research_warnings(warnings)
        payload = {"status": "error", "error": str(error), "details": error.payload}
        if warnings:
            payload["warnings"] = list(warnings)
        return emit_json(payload, exit_code=1)
    except Exception as error:  # defensive CLI boundary
        # Keep the exception type (and, under KB_DEBUG, the traceback) instead of flattening
        # every failure to a bare message (finding #30).
        payload = {"status": "error", "error": str(error), "error_type": type(error).__name__}
        warnings = _error_warnings(args)
        _emit_research_warnings(warnings)
        if warnings:
            payload["warnings"] = list(warnings)
        if os.getenv("KB_DEBUG"):
            payload["traceback"] = traceback.format_exc()
        return emit_json(payload, exit_code=1)


def _build_parser() -> argparse.ArgumentParser:
    parser = _CliArgumentParser(prog="kb", description="knowledge-base pipeline CLI")
    parser.add_argument("--config", help="Optional TOML config path")
    subcommands = parser.add_subparsers(dest="command")

    platform = subcommands.add_parser("platform", help="Manage local runtime")
    platform_sub = platform.add_subparsers(dest="platform_command")
    platform_sub.add_parser("up", help="Start ArangoDB runtime").set_defaults(handler=_platform_up)
    platform_sub.add_parser("down", help="Stop ArangoDB runtime").set_defaults(handler=_platform_down)
    platform_sub.add_parser("health", help="Check ArangoDB runtime").set_defaults(handler=_platform_health)
    platform_sub.add_parser("bootstrap", help="Create ArangoDB schema/indexes").set_defaults(handler=_platform_bootstrap)

    ingest = subcommands.add_parser("ingest", help="Ingest data")
    ingest_sub = ingest.add_subparsers(dest="ingest_command")
    ingest_sub.add_parser("fixture", help="Load safe synthetic fixture").set_defaults(handler=_ingest_fixture)
    tellmeabout = ingest_sub.add_parser("tellmeabout-tech", help="Load public tellmeabout.tech blog posts")
    tellmeabout.add_argument("--input", help="Local RSS/Atom snapshot path")
    tellmeabout.add_argument("--feed-url", default=DEFAULT_FEED_URL, help="RSS/Atom feed URL")
    tellmeabout.set_defaults(handler=_ingest_tellmeabout_tech)
    book_cube = ingest_sub.add_parser("book-cube", help="Load public Книжный куб Telegram channel posts")
    book_cube.add_argument("--input", help="Local Telegram HTML/JSON snapshot path")
    book_cube.add_argument("--url", default=BOOK_CUBE_DEFAULT_URL, help="Telegram public preview URL")
    book_cube.set_defaults(handler=_ingest_book_cube)
    book_cube_archive = ingest_sub.add_parser("book-cube-archive", help="Load Книжный куб owner archive")
    book_cube_archive.add_argument("--archive", required=True, help="Telegram Desktop JSON export directory or .zip")
    book_cube_archive.set_defaults(handler=_ingest_book_cube_archive)
    medium_export = ingest_sub.add_parser("medium-export", help="Load Medium account export posts")
    medium_export.add_argument("--archive", required=True, help="Medium export directory or .zip")
    medium_export.add_argument("--include-drafts", action="store_true", help="Import draft posts as draft documents")
    medium_export.set_defaults(handler=_ingest_medium_export)

    index = subcommands.add_parser("index", help="Manage derived indexes")
    index_sub = index.add_subparsers(dest="index_command")
    rebuild = index_sub.add_parser("rebuild", help="Rebuild/check derived indexes")
    rebuild.add_argument(
        "--target", default="all", choices=["all", "text", "vector", "graph", "related", "embeddings", "communities"]
    )
    rebuild.set_defaults(handler=_index_rebuild)

    search = subcommands.add_parser("search", help="Run retrieval queries")
    search_sub = search.add_subparsers(dest="search_command")
    text = search_sub.add_parser("text", help="Run full-text search")
    text.add_argument("query")
    text.add_argument("--limit", type=int, default=10)
    text.add_argument("--source", help="Optional exact source_key filter")
    text.set_defaults(handler=_search_text)
    semantic = search_sub.add_parser("semantic", help="Run semantic search")
    semantic.add_argument("query")
    semantic.add_argument("--limit", type=int, default=10)
    semantic.add_argument("--source", help="Optional exact source_key filter")
    semantic.add_argument("--min-similarity", type=float, help=_MIN_SIMILARITY_HELP)
    semantic.set_defaults(handler=_search_semantic)
    hybrid = search_sub.add_parser("hybrid", help="Run hybrid search")
    hybrid.add_argument("query")
    hybrid.add_argument("--limit", type=int, default=10)
    hybrid.add_argument("--source", help="Optional exact source_key filter")
    hybrid.add_argument("--min-similarity", type=float, help=_MIN_SIMILARITY_HELP)
    hybrid.set_defaults(handler=_search_hybrid)
    local = search_sub.add_parser("local", help="Run GraphRAG local search (entity subgraph around hits)")
    local.add_argument("query")
    local.add_argument("--limit", type=int, default=10)
    local.add_argument("--source", help="Optional exact source_key filter")
    local.add_argument("--min-similarity", type=float, help=_MIN_SIMILARITY_HELP)
    local.set_defaults(handler=_search_local)
    global_search_cmd = search_sub.add_parser("global", help="Run GraphRAG global search (community summaries)")
    global_search_cmd.add_argument("query")
    global_search_cmd.add_argument("--limit", type=int, default=10, help="Documents shown per community")
    global_search_cmd.add_argument("--communities", type=int, default=5, help="Number of communities to return")
    global_search_cmd.add_argument("--source", help="Optional exact source_key filter")
    global_search_cmd.add_argument("--min-similarity", type=float, help=_MIN_SIMILARITY_HELP)
    global_search_cmd.set_defaults(handler=_search_global)

    graph = subcommands.add_parser("graph", help="Run graph queries")
    graph_sub = graph.add_subparsers(dest="graph_command")
    neighbors = graph_sub.add_parser("neighbors", help="Return graph neighbors")
    start = neighbors.add_mutually_exclusive_group(required=True)
    start.add_argument("--topic")
    start.add_argument("--author")
    start.add_argument("--work")
    start.add_argument("--document")
    start.add_argument("--chunk")
    neighbors.add_argument("--limit", type=int, default=10)
    neighbors.add_argument("--source", help="Optional exact source_key filter")
    neighbors.add_argument("--documents-only", action="store_true", help="Return distinct document results only")
    neighbors.set_defaults(handler=_graph_neighbors)

    export = subcommands.add_parser("export", help="Export data")
    export_sub = export.add_subparsers(dest="export_command")
    jsonl = export_sub.add_parser("jsonl", help="Export documents/chunks as JSONL")
    jsonl.add_argument("--output", required=True)
    jsonl.set_defaults(handler=_export_jsonl)

    graph_export = export_sub.add_parser("graph", help="Export the document/topic graph as node-link JSON or GraphML")
    graph_export.add_argument("--format", choices=["json", "graphml"], default="json")
    graph_export.add_argument("--output", required=True)
    graph_export.add_argument("--ego", help="Optional document key for a bounded ego export")
    graph_export.add_argument("--topic-min-documents", type=int, default=2)
    graph_export.add_argument("--include-drafts", action="store_true")
    graph_export.set_defaults(handler=_export_graph)

    viz = subcommands.add_parser("viz", help="Build offline knowledge-base visualizations")
    viz_sub = viz.add_subparsers(dest="viz_command")
    viz_build = viz_sub.add_parser("build", help="Build a self-contained offline HTML visualization")
    viz_build.add_argument("--output", default=str(DEFAULT_VIZ_OUTPUT))
    viz_build.add_argument("--timeline-top-topics", type=int, default=10)
    viz_build.add_argument("--include-drafts", action="store_true")
    viz_build.set_defaults(handler=_viz_build)

    research = subcommands.add_parser("research", help="Build and manage research dossiers")
    research_sub = research.add_subparsers(dest="research_command")
    research_build = research_sub.add_parser("build", help="Build an immutable evidence dossier")
    research_build.add_argument("topic")
    research_build.add_argument("--output-root", help="Dossier output root (default: data/generated/research)")
    research_build.add_argument("--acknowledge-unsafe-output", action="store_true")
    research_build.add_argument("--source", help="Optional exact source_key filter")
    research_build.add_argument("--published-from", help="Inclusive UTC date (YYYY-MM-DD)")
    research_build.add_argument("--published-to", help="Inclusive UTC date (YYYY-MM-DD)")
    research_build.add_argument("--documents", type=int, default=12)
    research_build.add_argument("--fragments-per-document", type=int, default=2)
    research_build.add_argument("--include-drafts", action="store_true")
    research_build.set_defaults(handler=_research_build)

    research_validate = research_sub.add_parser("validate", help="Validate an immutable research artifact")
    research_validate.add_argument("artifact")
    research_validate.add_argument("--output-root", help="Research artifact root for related local artifacts")
    research_validate.set_defaults(handler=_research_validate)

    research_curate = research_sub.add_parser("curate", help="Create an immutable child dossier revision")
    research_curate.add_argument("revision")
    for operation in ("include", "exclude", "pin"):
        research_curate.add_argument(
            f"--{operation}",
            dest="curation_operations",
            action=_CurationOperationAction,
            metavar="CITATION_ID",
        )
    research_curate.add_argument("--reason", help="Optional owner note applied to every ordered operation")
    research_curate.add_argument("--output-root", help="Dossier output root (default: data/generated/research)")
    research_curate.add_argument("--acknowledge-unsafe-output", action="store_true")
    research_curate.set_defaults(handler=_research_curate)

    return parser


def _settings(args: argparse.Namespace):
    return load_settings(args.config)


def _repo(args: argparse.Namespace) -> KnowledgeRepository:
    settings = _settings(args)
    return KnowledgeRepository(ArangoClient(settings))


def _platform_up(args: argparse.Namespace) -> int:
    result = platform_up(_settings(args))
    return emit_json(result, exit_code=0 if result["status"] == "started" else 1)


def _platform_down(args: argparse.Namespace) -> int:
    result = platform_down(_settings(args))
    return emit_json(result, exit_code=0 if result["status"] == "stopped" else 1)


def _platform_health(args: argparse.Namespace) -> int:
    settings = _settings(args)
    report = health_report(ArangoClient(settings))
    # Exit non-zero when a core component (server, collections, view, graph) is missing so
    # scripted readiness gates fail; a degraded-only vector index is tolerated because
    # semantic search falls back to a full scan (finding #32).
    core_ready = report["status"] != "error" and all(
        check["status"] == "ok" for check in report.get("checks", []) if check["name"] != "vector_index"
    )
    return emit_json(report, exit_code=0 if core_ready else 1)


def _platform_bootstrap(args: argparse.Namespace) -> int:
    settings = _settings(args)
    bootstrap = bootstrap_schema(ArangoClient(settings), embedding_dimension=settings.embedding_dimension)
    return emit_json({"status": "ok", "bootstrap": bootstrap})


def _ingest_fixture(args: argparse.Namespace) -> int:
    settings = _settings(args)
    return emit_json(ingest_fixture(_repo(args), settings))


def _ingest_tellmeabout_tech(args: argparse.Namespace) -> int:
    settings = _settings(args)
    input_path = Path(args.input) if args.input else None
    result = ingest_tellmeabout_tech(_repo(args), settings, input_path=input_path, feed_url=args.feed_url)
    return emit_json(result, exit_code=0 if result["status"] == "ok" else 1)


def _ingest_book_cube(args: argparse.Namespace) -> int:
    settings = _settings(args)
    input_path = Path(args.input) if args.input else None
    result = ingest_book_cube(_repo(args), settings, input_path=input_path, url=args.url)
    return emit_json(result, exit_code=0 if result["status"] == "ok" else 1)


def _ingest_book_cube_archive(args: argparse.Namespace) -> int:
    settings = _settings(args)
    result = ingest_book_cube_archive(_repo(args), settings, archive_path=Path(args.archive))
    return emit_json(result, exit_code=0 if result["status"] == "ok" else 1)


def _ingest_medium_export(args: argparse.Namespace) -> int:
    settings = _settings(args)
    result = ingest_medium_export(
        _repo(args),
        settings,
        archive_path=Path(args.archive),
        include_drafts=args.include_drafts,
    )
    return emit_json(result, exit_code=0 if result["status"] == "ok" else 1)


def _index_rebuild(args: argparse.Namespace) -> int:
    settings = _settings(args)
    return emit_json(
        rebuild_indexes(
            _repo(args),
            target=args.target,
            embedding_dimension=settings.embedding_dimension,
            settings=settings,
        ),
    )


def _search_text(args: argparse.Namespace) -> int:
    return emit_json(text_search(_repo(args), args.query, limit=args.limit, source_key=args.source))


def _min_similarity(args: argparse.Namespace, settings) -> float:
    override = getattr(args, "min_similarity", None)
    return override if override is not None else settings.retrieval_min_similarity


def _search_semantic(args: argparse.Namespace) -> int:
    settings = _settings(args)
    return emit_json(
        semantic_search(
            _repo(args),
            args.query,
            limit=args.limit,
            source_key=args.source,
            provider=build_embedding_provider(settings),
            min_similarity=_min_similarity(args, settings),
        ),
    )


def _search_hybrid(args: argparse.Namespace) -> int:
    settings = _settings(args)
    return emit_json(
        hybrid_search(
            _repo(args),
            args.query,
            limit=args.limit,
            source_key=args.source,
            provider=build_embedding_provider(settings),
            min_similarity=_min_similarity(args, settings),
        ),
    )


def _search_local(args: argparse.Namespace) -> int:
    settings = _settings(args)
    return emit_json(
        local_search(
            _repo(args),
            args.query,
            limit=args.limit,
            source_key=args.source,
            provider=build_embedding_provider(settings),
            min_similarity=_min_similarity(args, settings),
        ),
    )


def _search_global(args: argparse.Namespace) -> int:
    settings = _settings(args)
    return emit_json(
        global_search(
            _repo(args),
            args.query,
            limit=args.limit,
            community_limit=args.communities,
            source_key=args.source,
            provider=build_embedding_provider(settings),
            min_similarity=_min_similarity(args, settings),
        ),
    )


def _graph_neighbors(args: argparse.Namespace) -> int:
    return emit_json(
        graph_neighbors(
            _repo(args),
            topic=args.topic,
            author=args.author,
            work=args.work,
            document=args.document,
            chunk=args.chunk,
            limit=args.limit,
            source_key=args.source,
            documents_only=args.documents_only,
        ),
    )


def _export_jsonl(args: argparse.Namespace) -> int:
    return emit_json(export_jsonl(_repo(args), Path(args.output)))


def _export_graph(args: argparse.Namespace) -> int:
    result = export_graph(
        _repo(args),
        Path(args.output),
        output_format=args.format,
        include_drafts=args.include_drafts,
        topic_min_documents=args.topic_min_documents,
        ego_document_key=args.ego,
    )
    return emit_json(result, exit_code=0 if result["status"] == "ok" else 1)


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
    settings = _settings(args)
    generated_root = Path(settings.repo_root) / "data" / "generated"
    output_root = Path(args.output_root).expanduser() if args.output_root is not None else generated_root / "research"
    location_warning = validate_output_root(
        output_root,
        generated_root=generated_root,
        acknowledge_unsafe=args.acknowledge_unsafe_output,
    )
    args._error_warnings = _research_warning_codes((location_warning,))

    repository = KnowledgeRepository(ArangoClient(settings))
    provider = build_embedding_provider(settings)
    result = build_dossier(
        repository,
        request,
        provider=provider,
        built_at=_utc_timestamp(),
    )
    args._error_warnings = _research_warning_codes(result.warnings, (location_warning,))
    if not result.publishable:
        warnings = _research_warning_codes(result.warnings, (location_warning,))
        _emit_research_warnings(warnings)
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
    warnings = _research_warning_codes(manifest["warnings"], (location_warning,))
    _emit_research_warnings(warnings)
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
    package = load_dossier_package(Path(args.artifact).expanduser())
    revision = DossierRevision(**package.manifest)
    result = validate_dossier_revision(
        _repo(args),
        revision,
        validated_at=_utc_timestamp(),
    )
    warnings = _research_warning_codes(result.warnings)
    args._error_warnings = warnings
    _emit_research_warnings(warnings)
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

    settings = _settings(args)
    generated_root = Path(settings.repo_root) / "data" / "generated"
    output_root = Path(args.output_root).expanduser() if args.output_root is not None else generated_root / "research"
    location_warning = validate_output_root(
        output_root,
        generated_root=generated_root,
        acknowledge_unsafe=args.acknowledge_unsafe_output,
    )
    args._error_warnings = _research_warning_codes((location_warning,))

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
        warnings = _research_warning_codes(validation_warnings, (location_warning,))
        args._error_warnings = warnings
        _emit_research_warnings(warnings)
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
    warnings = _research_warning_codes(manifest["warnings"], (location_warning,))
    args._error_warnings = warnings
    _emit_research_warnings(warnings)
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


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _research_warning_codes(*groups) -> tuple[str, ...]:
    values: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in group:
            if isinstance(value, str) and value and value not in seen:
                seen.add(value)
                values.append(value)
    return tuple(values)


def _error_warnings(args: argparse.Namespace | None) -> tuple[str, ...]:
    if args is None:
        return ()
    return _research_warning_codes(getattr(args, "_error_warnings", ()))


def _emit_research_warnings(warnings: tuple[str, ...]) -> None:
    for warning in warnings:
        message = _RESEARCH_WARNING_MESSAGES.get(warning)
        suffix = f": {message}" if message is not None else ""
        sys.stderr.write(f"warning: {warning}{suffix}\n")


def _viz_build(args: argparse.Namespace) -> int:
    result = build_visualization(
        _repo(args),
        Path(args.output),
        timeline_top_topics=args.timeline_top_topics,
        include_drafts=args.include_drafts,
    )
    return emit_json(result, exit_code=0 if result["status"] == "ok" else 1)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
