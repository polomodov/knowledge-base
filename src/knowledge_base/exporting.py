from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from knowledge_base.config import REPO_ROOT
from knowledge_base.repository import KnowledgeRepository


def _export_zone_warning(output: Path) -> str | None:
    # The export contains full document and chunk text; warn when it lands outside the
    # gitignored data/generated zone where it could be committed or shared (finding #39).
    generated_zone = (REPO_ROOT / "data" / "generated").resolve()
    if output.resolve().is_relative_to(generated_zone):
        return None
    sys.stderr.write(
        f"warning: JSONL export contains full personal document and chunk text; writing "
        f"outside data/generated/ ({output}) risks committing or sharing it.\n",
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
