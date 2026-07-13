"""Shared CLI helpers: settings, repository, usage errors, warning emission."""

from __future__ import annotations

import argparse
import sys

from knowledge_base.arango import ArangoClient
from knowledge_base.config import load_settings
from knowledge_base.repository import KnowledgeRepository

# Research warning messages shared by main error boundary and research handlers.
_RESEARCH_WARNING_MESSAGES = {
    "draft_visibility_enabled": "draft evidence visibility is enabled; exact excerpts may contain private material",
    "output_outside_generated_zone": "output root is outside data/generated; explicit acknowledgement was accepted",
}


class CliUsageError(ValueError):
    """Command-line syntax or command selection is invalid."""


def _settings(args: argparse.Namespace):
    return load_settings(args.config)


def _repo(args: argparse.Namespace) -> KnowledgeRepository:
    settings = _settings(args)
    return KnowledgeRepository(ArangoClient(settings))


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
