from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from knowledge_base.arango import ArangoClient, ArangoError
from knowledge_base.config import load_settings
from knowledge_base.exporting import export_jsonl
from knowledge_base.fixture import ingest_fixture
from knowledge_base.indexing import rebuild_indexes
from knowledge_base.json_output import emit_json
from knowledge_base.platform import platform_down, platform_up
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.retrieval import graph_neighbors, hybrid_search, semantic_search, text_search
from knowledge_base.schema import bootstrap_schema, health_report


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 2

    try:
        return args.handler(args)
    except ArangoError as error:
        return emit_json({"status": "error", "error": str(error), "details": error.payload}, exit_code=1)
    except Exception as error:  # pragma: no cover - defensive CLI boundary
        return emit_json({"status": "error", "error": str(error)}, exit_code=1)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kb", description="knowledge-base pipeline CLI")
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

    index = subcommands.add_parser("index", help="Manage derived indexes")
    index_sub = index.add_subparsers(dest="index_command")
    rebuild = index_sub.add_parser("rebuild", help="Rebuild/check derived indexes")
    rebuild.add_argument("--target", default="all", choices=["all", "text", "vector", "graph"])
    rebuild.set_defaults(handler=_index_rebuild)

    search = subcommands.add_parser("search", help="Run retrieval queries")
    search_sub = search.add_subparsers(dest="search_command")
    text = search_sub.add_parser("text", help="Run full-text search")
    text.add_argument("query")
    text.add_argument("--limit", type=int, default=10)
    text.set_defaults(handler=_search_text)
    semantic = search_sub.add_parser("semantic", help="Run semantic search")
    semantic.add_argument("query")
    semantic.add_argument("--limit", type=int, default=10)
    semantic.set_defaults(handler=_search_semantic)
    hybrid = search_sub.add_parser("hybrid", help="Run hybrid search")
    hybrid.add_argument("query")
    hybrid.add_argument("--limit", type=int, default=10)
    hybrid.set_defaults(handler=_search_hybrid)

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
    neighbors.set_defaults(handler=_graph_neighbors)

    export = subcommands.add_parser("export", help="Export data")
    export_sub = export.add_subparsers(dest="export_command")
    jsonl = export_sub.add_parser("jsonl", help="Export documents/chunks as JSONL")
    jsonl.add_argument("--output", required=True)
    jsonl.set_defaults(handler=_export_jsonl)

    return parser


def _settings(args: argparse.Namespace):
    return load_settings(args.config)


def _repo(args: argparse.Namespace) -> KnowledgeRepository:
    settings = _settings(args)
    return KnowledgeRepository(ArangoClient(settings))


def _platform_up(args: argparse.Namespace) -> int:
    return emit_json(platform_up(_settings(args)), exit_code=0)


def _platform_down(args: argparse.Namespace) -> int:
    return emit_json(platform_down(_settings(args)), exit_code=0)


def _platform_health(args: argparse.Namespace) -> int:
    settings = _settings(args)
    report = health_report(ArangoClient(settings))
    return emit_json(report, exit_code=0 if report["status"] in {"ok", "degraded"} else 1)


def _platform_bootstrap(args: argparse.Namespace) -> int:
    settings = _settings(args)
    return emit_json({"status": "ok", "bootstrap": bootstrap_schema(ArangoClient(settings))})


def _ingest_fixture(args: argparse.Namespace) -> int:
    settings = _settings(args)
    return emit_json(ingest_fixture(_repo(args), settings))


def _index_rebuild(args: argparse.Namespace) -> int:
    return emit_json(rebuild_indexes(_repo(args), target=args.target))


def _search_text(args: argparse.Namespace) -> int:
    return emit_json(text_search(_repo(args), args.query, limit=args.limit))


def _search_semantic(args: argparse.Namespace) -> int:
    settings = _settings(args)
    return emit_json(semantic_search(_repo(args), args.query, limit=args.limit, dimension=settings.embedding_dimension))


def _search_hybrid(args: argparse.Namespace) -> int:
    settings = _settings(args)
    return emit_json(hybrid_search(_repo(args), args.query, limit=args.limit, dimension=settings.embedding_dimension))


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
        ),
    )


def _export_jsonl(args: argparse.Namespace) -> int:
    return emit_json(export_jsonl(_repo(args), Path(args.output)))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
