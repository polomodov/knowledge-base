from pathlib import Path
from zipfile import ZipFile

from knowledge_base.sources.book_cube import (
    ArchiveReadError,
    collect_attachment_refs,
    parse_snapshot,
    read_archive_payload,
)


ARCHIVE_DIR = Path("tests/fixtures/book_cube_owner_export")


def test_read_archive_payload_from_directory() -> None:
    archive = read_archive_payload(ARCHIVE_DIR)

    assert archive.kind == "directory"
    assert archive.ref == str(ARCHIVE_DIR)
    assert archive.result_json.endswith("result.json")
    assert archive.result_sha256
    assert archive.manifest_sha256
    assert archive.snapshot.payload.startswith("{")
    assert archive.snapshot.media_type == "application/json"


def test_read_archive_payload_from_zip_with_nested_result_json(tmp_path: Path) -> None:
    zip_path = tmp_path / "book-cube-export.zip"
    with ZipFile(zip_path, "w") as archive:
        for path in ARCHIVE_DIR.rglob("*"):
            if path.is_file():
                archive.write(path, Path("Book Cube Export") / path.relative_to(ARCHIVE_DIR))

    payload = read_archive_payload(zip_path)

    assert payload.kind == "zip"
    assert payload.result_json == "Book Cube Export/result.json"
    assert payload.manifest_sha256
    assert payload.snapshot.storage_kind == "local_file"


def test_parse_owner_archive_captions_and_attachments() -> None:
    archive = read_archive_payload(ARCHIVE_DIR)
    parsed = parse_snapshot(archive.snapshot.payload, media_type="application/json", archive=archive)

    assert len(parsed.items) == 3
    assert parsed.skipped == [
        {"guid": "303", "reason": "empty_text"},
        {"guid": "304", "reason": "unsupported_type"},
    ]
    assert parsed.items[0].canonical_id == "book_cube-301"
    assert parsed.items[0].tags == ["archive", "books"]
    assert parsed.items[0].metadata["attachments"][0]["field"] == "photo"
    assert parsed.items[0].metadata["attachments"][0]["relative_path"] == "photos/photo_301.jpg"
    assert parsed.items[0].metadata["attachments"][0]["size_bytes"] > 0

    caption_item = parsed.items[1]
    assert "Caption к файлу" in caption_item.text
    assert caption_item.tags == ["reading"]
    assert caption_item.metadata["attachments"][0]["mime_type"] == "application/pdf"


def test_collect_attachment_refs_without_archive() -> None:
    refs = collect_attachment_refs({"photo": "photos/photo.jpg", "media_type": "photo"}, archive=None)
    assert refs == [
        {
            "field": "photo",
            "relative_path": "photos/photo.jpg",
            "local_path": None,
            "media_type": "photo",
            "mime_type": None,
            "size_bytes": None,
        },
    ]


def test_archive_errors_have_cli_payload() -> None:
    error = ArchiveReadError("result_json_not_found", Path("missing"))
    payload = error.to_payload()
    assert payload["status"] == "error"
    assert payload["error"] == "result_json_not_found"
    assert payload["source_key"] == "book-cube"
