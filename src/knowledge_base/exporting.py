from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from knowledge_base.repository import KnowledgeRepository


def export_jsonl(repository: KnowledgeRepository, output: Path) -> dict[str, Any]:
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
    return {"status": "ok", "output": str(output), "records": len(rows)}
