from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from knowledge_base.config import REPO_ROOT
from knowledge_base.repository import KnowledgeRepository


def _export_zone_warning(
    output: Path,
    *,
    content: str = "full personal document and chunk text",
) -> str | None:
    """Warn when a derived artifact leaves the repository's gitignored generated zone.

    Different export surfaces disclose different material. JSONL contains normalized text, while
    graph and visualization artifacts contain titles, URLs and corpus topology. Keeping the content
    description at the call site makes the warning honest without turning it into a write blocker.
    """
    generated_zone = (REPO_ROOT / "data" / "generated").resolve()
    if output.resolve().is_relative_to(generated_zone):
        return None
    sys.stderr.write(
        f"warning: export contains {content}; writing outside data/generated/ ({output}) risks committing or sharing it.\n",
    )
    return "output_outside_generated_zone"


def export_jsonl(repository: KnowledgeRepository, output: Path) -> dict[str, Any]:
    warning = _export_zone_warning(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = repository.client.aql(
        """
        FOR doc IN documents
          SORT doc._key ASC
          LET source = DOCUMENT("sources", doc.source_key)
          LET chunks = (
            FOR chunk IN chunks
              FILTER chunk.document_key == doc._key
              SORT chunk.ordinal ASC
              RETURN KEEP(chunk, ["_key", "ordinal", "text", "token_count", "metadata"])
          )
          RETURN { document: doc, source: source, chunks: chunks }
        """,
    )
    # Deterministic outer order (SORT doc._key) plus an atomic write keep the export byte-stable
    # across runs, so downstream change-detection can hash/diff it, and a mid-write failure never
    # leaves a partially written file at the destination.
    payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    _atomic_write_text(output, payload)
    result: dict[str, Any] = {"status": "ok", "output": str(output), "records": len(rows)}
    if warning:
        result["warning"] = warning
    return result


def _atomic_write_text(output: Path, payload: str) -> None:
    """Write ``payload`` to ``output`` via a same-directory temp file and atomic rename."""
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
