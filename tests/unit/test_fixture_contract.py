import json
from pathlib import Path


def test_safe_fixture_contains_no_personal_source_names() -> None:
    fixture = json.loads(Path("tests/fixtures/safe_knowledge_fixture.json").read_text(encoding="utf-8"))
    assert fixture["source"]["type"] == "manual_fixture"
    assert fixture["source"]["metadata"]["description"] == "Synthetic fixture with no personal data."
    assert fixture["documents"]


def test_query_output_schema_is_valid_json() -> None:
    schema = json.loads(
        Path("specs/001-production-knowledge-pipeline/contracts/query-output.schema.json").read_text(
            encoding="utf-8",
        ),
    )
    assert schema["title"] == "Knowledge Retrieval Result"
    assert "results" in schema["required"]
