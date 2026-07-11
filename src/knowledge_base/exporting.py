from __future__ import annotations

import json
import sys
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
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    result: dict[str, Any] = {"status": "ok", "output": str(output), "records": len(rows)}
    if warning:
        result["warning"] = warning
    return result
