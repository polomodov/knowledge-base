from pathlib import Path
from zipfile import ZipFile

import pytest

from knowledge_base.sources.medium_export import (
    MediumArchiveReadError,
    _MediumPostHTMLParser,
    canonical_id_from_post_id,
    medium_post_id_from_url,
    parse_medium_archive,
    read_medium_archive_payload,
)


def test_author_link_with_avatar_image_does_not_swallow_body() -> None:
    # An <img> avatar inside the p-author anchor has no end tag; author capture must still
    # close on </a> instead of absorbing the following text (finding #23).
    parser = _MediumPostHTMLParser()
    parser.feed(
        "<html><head><title>Post</title></head><body>"
        '<section data-field="body"><p>Body paragraph one.</p><p>Body two.</p></section>'
        '<footer><p>By <a class="p-author h-card" href="https://medium.com/@a">'
        '<img src="avatar.jpg">Alice</a> on June 1, 2026.</p></footer></body></html>'
    )
    parser.close()
    assert parser.author == "Alice"
    assert "Body paragraph one." in parser.text
    assert "Body two." in parser.text


def test_body_survives_stray_void_end_tag() -> None:
    # A stray </br> must not decrement the body depth it never opened and truncate the article
    # (finding #4).
    parser = _MediumPostHTMLParser()
    parser.feed('<html><body><section data-field="body"><p>Part one.</p></br><p>Part two.</p></section></body></html>')
    parser.close()
    assert "Part one." in parser.text
    assert "Part two." in parser.text


ARCHIVE_DIR = Path("tests/fixtures/medium_export")


def test_read_medium_archive_payload_from_directory() -> None:
    archive = read_medium_archive_payload(ARCHIVE_DIR)
    repeat = read_medium_archive_payload(ARCHIVE_DIR)

    assert archive.kind == "directory"
    assert archive.ref == str(ARCHIVE_DIR)
    assert archive.manifest_sha256 == repeat.manifest_sha256
    assert archive.total_files == 4
    assert len(archive.posts) == 3
    assert archive.posts[0].relative_path.startswith("posts/")
    assert archive.manifest_json.startswith("[")


def test_read_medium_archive_payload_from_zip_with_nested_root(tmp_path: Path) -> None:
    zip_path = tmp_path / "medium-export.zip"
    with ZipFile(zip_path, "w") as archive:
        for path in ARCHIVE_DIR.rglob("*"):
            if path.is_file():
                archive.write(path, Path("Medium Export") / path.relative_to(ARCHIVE_DIR))

    payload = read_medium_archive_payload(zip_path)

    assert payload.kind == "zip"
    assert payload.root == "Medium Export"
    assert payload.manifest_sha256
    assert len(payload.posts) == 3
    assert {post.relative_path for post in payload.posts} == {
        "posts/2026-06-01_First-Medium-Export-Post-abc123abc123.html",
        "posts/2026-06-02_Second-Medium-Export-Post-def456def456.html",
        "posts/draft_Draft-Medium-Export-Post-fed456fed456.html",
    }


def test_parse_medium_archive_published_posts_by_default() -> None:
    archive = read_medium_archive_payload(ARCHIVE_DIR)
    parsed = parse_medium_archive(archive)

    assert parsed.title == "Medium Export"
    assert len(parsed.items) == 2
    assert parsed.skipped == [{"guid": "fed456fed456", "reason": "draft_excluded"}]

    first = parsed.items[0]
    assert first.canonical_id == "medium-post-abc123abc123"
    assert first.title == "First Medium Export Post"
    assert first.url == "https://medium.com/@apolomodov/first-medium-export-post-abc123abc123"
    assert first.guid == "abc123abc123"
    assert first.published_at == "2026-06-01T10:00:00Z"
    assert first.author == "Alexander Polomodov"
    assert first.tags == []
    assert "preserves provenance" in first.text
    assert first.metadata["status"] == "published"
    assert first.metadata["medium_post"]["post_id"] == "abc123abc123"
    assert first.metadata["medium_post"]["local_post_path"].startswith("posts/")
    assert first.metadata["medium_post"]["exported_at"] == "2026-07-06"
    assert first.metadata["images"][0]["src"].startswith("https://cdn-images-1.medium.com/")
    assert first.metadata["links"] == [
        "https://example.com/reference",
        "https://medium.com/@someone/linked-post-999999999999",
    ]


def test_parse_medium_archive_can_include_drafts() -> None:
    archive = read_medium_archive_payload(ARCHIVE_DIR)
    parsed = parse_medium_archive(archive, include_drafts=True)

    assert len(parsed.items) == 3
    assert parsed.skipped == []
    draft = next(item for item in parsed.items if item.guid == "fed456fed456")
    assert draft.metadata["status"] == "draft"
    assert draft.published_at is None
    assert draft.url == "https://medium.com/p/fed456fed456"


def test_medium_post_helpers_are_stable() -> None:
    assert medium_post_id_from_url("https://medium.com/p/abc123abc123") == "abc123abc123"
    assert medium_post_id_from_url("https://medium.com/@user/my-post-def456def456") == "def456def456"
    assert medium_post_id_from_url("https://example.com/not-medium-abc123abc123") is None
    assert canonical_id_from_post_id("abc123abc123") == "medium-post-abc123abc123"


def test_archive_errors_have_cli_payload(tmp_path: Path) -> None:
    with pytest.raises(MediumArchiveReadError) as missing:
        read_medium_archive_payload(tmp_path / "missing")
    assert missing.value.to_payload()["error"] == "archive_not_readable"

    no_posts = tmp_path / "no-posts"
    no_posts.mkdir()
    (no_posts / "README.html").write_text("<html></html>", encoding="utf-8")
    with pytest.raises(MediumArchiveReadError) as posts:
        read_medium_archive_payload(no_posts)
    assert posts.value.to_payload()["error"] == "posts_not_found"

    invalid = tmp_path / "invalid"
    (invalid / "posts").mkdir(parents=True)
    (invalid / "posts" / "post.html").write_text("<html></html>", encoding="utf-8")
    with pytest.raises(MediumArchiveReadError) as export:
        read_medium_archive_payload(invalid)
    assert export.value.to_payload()["error"] == "invalid_medium_export"
