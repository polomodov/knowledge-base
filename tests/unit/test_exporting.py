import json
from pathlib import Path

from knowledge_base.config import REPO_ROOT
from knowledge_base.exporting import _export_zone_warning, export_jsonl


class _StubClient:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def aql(self, query: str, bind_vars: dict | None = None) -> list[dict]:
        return self.rows


class _StubRepository:
    def __init__(self, rows: list[dict]) -> None:
        self.client = _StubClient(rows)


def test_export_jsonl_writes_one_json_object_per_line(tmp_path: Path) -> None:
    rows = [
        {"document": {"_key": "d1", "text": "Привет мир"}, "source": {}, "chunks": []},
        {"document": {"_key": "d2", "text": "hello"}, "source": {}, "chunks": []},
    ]
    output = tmp_path / "nested" / "export.jsonl"
    result = export_jsonl(_StubRepository(rows), output)

    assert result["records"] == 2
    text = output.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["document"]["_key"] for line in lines] == ["d1", "d2"]
    assert "Привет мир" in text  # non-ascii preserved (ensure_ascii=False)


def test_export_jsonl_warns_outside_generated_zone(tmp_path: Path) -> None:
    result = export_jsonl(_StubRepository([]), tmp_path / "leak.jsonl")
    assert result["warning"] == "output_outside_generated_zone"


def test_export_zone_warning_silent_inside_generated_zone() -> None:
    inside = REPO_ROOT / "data" / "generated" / "exports" / "fixture.jsonl"
    assert _export_zone_warning(inside) is None
