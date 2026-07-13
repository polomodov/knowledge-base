"""knowledge-base CLI: argparse wiring and dispatch."""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from typing import Never

from knowledge_base.arango import ArangoError
from knowledge_base.cli.common import CliUsageError, _emit_research_warnings, _error_warnings

# Re-exports used by unit tests and external callers.
from knowledge_base.cli.common import _repo as _repo
from knowledge_base.cli.common import _settings as _settings
from knowledge_base.cli.platform_cmd import (
    _index_rebuild,
    _ingest_book_cube,
    _ingest_book_cube_archive,
    _ingest_fixture,
    _ingest_medium_export,
    _ingest_tellmeabout_tech,
    _platform_bootstrap,
    _platform_down,
    _platform_health,
    _platform_up,
)
from knowledge_base.cli.research_cmd import (
    _CurationOperationAction,
    _research_build,
    _research_curate,
    _research_handoff,
    _research_import_output,
    _research_validate,
)
from knowledge_base.cli.search_cmd import (
    _MIN_SIMILARITY_HELP,
    _graph_neighbors,
    _search_global,
    _search_hybrid,
    _search_local,
    _search_semantic,
    _search_text,
)
from knowledge_base.cli.viz_cmd import _export_graph, _export_jsonl, _viz_build
from knowledge_base.json_output import emit_json
from knowledge_base.research_workflow import DossierCurationError as DossierCurationError
from knowledge_base.sources.book_cube import DEFAULT_PUBLIC_URL as BOOK_CUBE_DEFAULT_URL
from knowledge_base.sources.tellmeabout_tech import DEFAULT_FEED_URL
from knowledge_base.viz_builder import DEFAULT_VIZ_OUTPUT
from knowledge_base.writing_handoff import WritingHandoffError as WritingHandoffError
from knowledge_base.writing_handoff import WritingImportError as WritingImportError


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
    research_validate.add_argument("--handoff", help="Required trusted local handoff for incoming writing output")
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

    research_handoff = research_sub.add_parser("handoff", help="Create a writing-agent handoff package")
    research_handoff.add_argument("revision")
    research_handoff.add_argument("--output-root", help="Research artifact root (default: data/generated/research)")
    research_handoff.add_argument("--output-kind", choices=["draft", "summary"], default="draft")
    research_handoff.add_argument("--language", default="ru")
    research_handoff.add_argument("--style")
    research_handoff.add_argument("--max-words", type=int)
    research_handoff.add_argument("--acknowledge-external-disclosure", action="store_true")
    research_handoff.add_argument("--acknowledge-unsafe-output", action="store_true")
    research_handoff.add_argument("--allow-draft-evidence", action="store_true")
    research_handoff.set_defaults(handler=_research_handoff)

    research_import = research_sub.add_parser("import-output", help="Validate and import writing-agent output")
    research_import.add_argument("package")
    research_import.add_argument("--handoff", required=True, help="Trusted local handoff used for this output")
    research_import.add_argument("--output-root", help="Research artifact root (default: data/generated/research)")
    research_import.add_argument("--acknowledge-unsafe-output", action="store_true")
    research_import.set_defaults(handler=_research_import_output)

    return parser


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
