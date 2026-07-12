"""Export and visualization CLI handlers."""

from __future__ import annotations

import argparse
from pathlib import Path

from knowledge_base.cli import common as cli_common
from knowledge_base.exporting import export_jsonl
from knowledge_base.graph_export import export_graph
from knowledge_base.json_output import emit_json
from knowledge_base.viz_builder import build_visualization


def _export_jsonl(args: argparse.Namespace) -> int:
    return emit_json(export_jsonl(cli_common._repo(args), Path(args.output)))


def _export_graph(args: argparse.Namespace) -> int:
    result = export_graph(
        cli_common._repo(args),
        Path(args.output),
        output_format=args.format,
        include_drafts=args.include_drafts,
        topic_min_documents=args.topic_min_documents,
        ego_document_key=args.ego,
    )
    return emit_json(result, exit_code=0 if result["status"] == "ok" else 1)


def _viz_build(args: argparse.Namespace) -> int:
    result = build_visualization(
        cli_common._repo(args),
        Path(args.output),
        timeline_top_topics=args.timeline_top_topics,
        include_drafts=args.include_drafts,
    )
    return emit_json(result, exit_code=0 if result["status"] == "ok" else 1)
