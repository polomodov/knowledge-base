"""Platform, ingest, and index CLI handlers."""

from __future__ import annotations

import argparse
from pathlib import Path

from knowledge_base.arango import ArangoClient
from knowledge_base.cli import common as cli_common
from knowledge_base.fixture import ingest_fixture
from knowledge_base.indexing import rebuild_indexes
from knowledge_base.json_output import emit_json
from knowledge_base.platform import platform_down, platform_up
from knowledge_base.schema import bootstrap_schema, health_report
from knowledge_base.sources.book_cube import ingest_book_cube, ingest_book_cube_archive
from knowledge_base.sources.medium_export import ingest_medium_export
from knowledge_base.sources.tellmeabout_tech import ingest_tellmeabout_tech


def _platform_up(args: argparse.Namespace) -> int:
    result = platform_up(cli_common._settings(args))
    return emit_json(result, exit_code=0 if result["status"] == "started" else 1)


def _platform_down(args: argparse.Namespace) -> int:
    result = platform_down(cli_common._settings(args))
    return emit_json(result, exit_code=0 if result["status"] == "stopped" else 1)


def _platform_health(args: argparse.Namespace) -> int:
    settings = cli_common._settings(args)
    report = health_report(ArangoClient(settings))
    # Exit non-zero when a core component (server, collections, view, graph) is missing so
    # scripted readiness gates fail; a degraded-only vector index is tolerated because
    # semantic search falls back to a full scan (finding #32).
    core_ready = report["status"] != "error" and all(
        check["status"] == "ok" for check in report.get("checks", []) if check["name"] != "vector_index"
    )
    return emit_json(report, exit_code=0 if core_ready else 1)


def _platform_bootstrap(args: argparse.Namespace) -> int:
    settings = cli_common._settings(args)
    bootstrap = bootstrap_schema(ArangoClient(settings), embedding_dimension=settings.embedding_dimension)
    return emit_json({"status": "ok", "bootstrap": bootstrap})


def _ingest_fixture(args: argparse.Namespace) -> int:
    settings = cli_common._settings(args)
    return emit_json(ingest_fixture(cli_common._repo(args), settings))


def _ingest_tellmeabout_tech(args: argparse.Namespace) -> int:
    settings = cli_common._settings(args)
    input_path = Path(args.input) if args.input else None
    result = ingest_tellmeabout_tech(cli_common._repo(args), settings, input_path=input_path, feed_url=args.feed_url)
    return emit_json(result, exit_code=0 if result["status"] == "ok" else 1)


def _ingest_book_cube(args: argparse.Namespace) -> int:
    settings = cli_common._settings(args)
    input_path = Path(args.input) if args.input else None
    result = ingest_book_cube(cli_common._repo(args), settings, input_path=input_path, url=args.url)
    return emit_json(result, exit_code=0 if result["status"] == "ok" else 1)


def _ingest_book_cube_archive(args: argparse.Namespace) -> int:
    settings = cli_common._settings(args)
    result = ingest_book_cube_archive(cli_common._repo(args), settings, archive_path=Path(args.archive))
    return emit_json(result, exit_code=0 if result["status"] == "ok" else 1)


def _ingest_medium_export(args: argparse.Namespace) -> int:
    settings = cli_common._settings(args)
    result = ingest_medium_export(
        cli_common._repo(args),
        settings,
        archive_path=Path(args.archive),
        include_drafts=args.include_drafts,
    )
    return emit_json(result, exit_code=0 if result["status"] == "ok" else 1)


def _index_rebuild(args: argparse.Namespace) -> int:
    settings = cli_common._settings(args)
    return emit_json(
        rebuild_indexes(
            cli_common._repo(args),
            target=args.target,
            embedding_dimension=settings.embedding_dimension,
            settings=settings,
        ),
    )
