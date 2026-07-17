"""Search and graph query CLI handlers."""

from __future__ import annotations

import argparse

from knowledge_base.cli import common as cli_common
from knowledge_base.embeddings import build_embedding_provider
from knowledge_base.json_output import emit_json
from knowledge_base.retrieval import (
    global_search,
    graph_neighbors,
    hybrid_search,
    local_search,
    semantic_search,
    text_search,
)

_MIN_SIMILARITY_HELP = "Relevance floor for semantic hits (default from config)"


def _search_text(args: argparse.Namespace) -> int:
    return emit_json(text_search(cli_common._repo(args), args.query, limit=args.limit, source_key=args.source))


def _min_similarity(args: argparse.Namespace, settings) -> float:
    override = getattr(args, "min_similarity", None)
    return override if override is not None else settings.retrieval_min_similarity


def _search_semantic(args: argparse.Namespace) -> int:
    settings = cli_common._settings(args)
    return emit_json(
        semantic_search(
            cli_common._repo(args),
            args.query,
            limit=args.limit,
            source_key=args.source,
            provider=build_embedding_provider(settings),
            min_similarity=_min_similarity(args, settings),
        ),
    )


def _search_hybrid(args: argparse.Namespace) -> int:
    settings = cli_common._settings(args)
    return emit_json(
        hybrid_search(
            cli_common._repo(args),
            args.query,
            limit=args.limit,
            source_key=args.source,
            provider=build_embedding_provider(settings),
            min_similarity=_min_similarity(args, settings),
        ),
    )


def _search_local(args: argparse.Namespace) -> int:
    settings = cli_common._settings(args)
    return emit_json(
        local_search(
            cli_common._repo(args),
            args.query,
            limit=args.limit,
            source_key=args.source,
            provider=build_embedding_provider(settings),
            min_similarity=_min_similarity(args, settings),
        ),
    )


def _search_global(args: argparse.Namespace) -> int:
    settings = cli_common._settings(args)
    return emit_json(
        global_search(
            cli_common._repo(args),
            args.query,
            limit=args.limit,
            community_limit=args.communities,
            source_key=args.source,
            provider=build_embedding_provider(settings),
            min_similarity=_min_similarity(args, settings),
        ),
    )


def _graph_neighbors(args: argparse.Namespace) -> int:
    result = graph_neighbors(
        cli_common._repo(args),
        topic=args.topic,
        author=args.author,
        work=args.work,
        document=args.document,
        chunk=args.chunk,
        limit=args.limit,
        source_key=args.source,
        documents_only=args.documents_only,
    )
    # graph_neighbors returns only "ok" or "error" (e.g. a missing start vertex); mirror
    # _export_graph/_viz_build so a failed query surfaces a non-zero exit code instead of 0.
    return emit_json(result, exit_code=0 if result["status"] == "ok" else 1)
