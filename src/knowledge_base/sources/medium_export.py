from __future__ import annotations

import json
import re
import urllib.parse
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from knowledge_base.chunking import split_text
from knowledge_base.config import Settings
from knowledge_base.embeddings import HASH_EMBEDDING_MODEL, hash_embedding
from knowledge_base.ids import chunk_key, document_key, sha256_text, slugify, stable_key
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.schema import bootstrap_schema
from knowledge_base.sources.contracts import NormalizedSourceItem, ParsedSourceFeed


SOURCE_KEY = "medium-export"
DISPLAY_NAME = "Medium Export"
ARCHIVE_HINT = "Copy Medium export under data/raw/medium/ and rerun with --archive."
README_NAME = "README.html"
POST_ID_RE = re.compile(r"([0-9a-f]{12,})$", re.IGNORECASE)
EXPORT_DATE_RE = re.compile(r"Exported from .*? on ([A-Za-z]+ \d{1,2}, \d{4})", re.DOTALL)
BLOCK_TAGS = {"article", "section", "p", "div", "h1", "h2", "h3", "h4", "li", "blockquote", "pre", "figure"}
VOID_TAGS = {"br", "hr", "img", "input", "meta", "link"}


@dataclass(frozen=True)
class MediumPostPayload:
    relative_path: str
    payload: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class MediumArchivePayload:
    kind: str
    ref: str
    manifest_sha256: str
    manifest_json: str
    posts: list[MediumPostPayload]
    total_files: int
    total_size_bytes: int
    root: str | None = None


class MediumArchiveReadError(RuntimeError):
    def __init__(self, error: str, archive: Path, detail: str | None = None) -> None:
        super().__init__(detail or error)
        self.error = error
        self.archive = archive
        self.detail = detail or error

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": "error",
            "error": self.error,
            "source_key": SOURCE_KEY,
            "archive": str(self.archive),
            "hint": ARCHIVE_HINT,
            "reason": self.detail,
        }


class _MediumPostHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.body_parts: list[str] = []
        self.author_parts: list[str] = []
        self.canonical_url: str | None = None
        self.post_url: str | None = None
        self.post_id: str | None = None
        self.published_at: str | None = None
        self.images: list[dict[str, Any]] = []
        self.links: list[str] = []
        self._in_title = False
        self._body_depth = 0
        self._author_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        classes = set((attr.get("class") or "").split())
        tag = tag.lower()

        if tag == "title":
            self._in_title = True

        if tag == "section" and attr.get("data-field") == "body" and self._body_depth == 0:
            self._body_depth = 1
            self.body_parts.append(" ")
            return

        if self._body_depth > 0:
            if tag not in VOID_TAGS:
                self._body_depth += 1
            if tag in BLOCK_TAGS or tag == "br":
                self.body_parts.append(" ")
            if tag == "a" and attr.get("href"):
                self.links.append(attr["href"])
            if tag == "img":
                image = {
                    "src": attr.get("src"),
                    "data_image_id": attr.get("data-image-id"),
                    "width": _int_or_none(attr.get("data-width")),
                    "height": _int_or_none(attr.get("data-height")),
                }
                if any(value is not None for value in image.values()):
                    self.images.append(image)

        if tag == "a" and attr.get("href"):
            href = attr["href"]
            if "p-canonical" in classes:
                self.canonical_url = href
                self.post_id = medium_post_id_from_url(href) or self.post_id
            else:
                parsed_href = urllib.parse.urlparse(href)
                if self._body_depth == 0 and "/p/" in parsed_href.path:
                    self.post_url = href
                    self.post_id = medium_post_id_from_url(href) or self.post_id

        if tag == "a" and "p-author" in classes:
            self._author_depth = 1
        elif self._author_depth > 0:
            self._author_depth += 1

        if tag == "time" and "dt-published" in classes and attr.get("datetime"):
            self.published_at = _parse_date(attr["datetime"])

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._body_depth > 0:
            self.body_parts.append(data)
        if self._author_depth > 0:
            self.author_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False

        if self._author_depth > 0:
            self._author_depth -= 1

        if self._body_depth > 0:
            if tag in BLOCK_TAGS:
                self.body_parts.append(" ")
            self._body_depth -= 1

    @property
    def title(self) -> str:
        return _clean_text("".join(self.title_parts))

    @property
    def text(self) -> str:
        return _clean_text("".join(self.body_parts))

    @property
    def author(self) -> str | None:
        return _clean_text("".join(self.author_parts)) or None


def ingest_medium_export(
    repository: KnowledgeRepository,
    settings: Settings,
    *,
    archive_path: Path,
    include_drafts: bool = False,
) -> dict[str, Any]:
    try:
        archive = read_medium_archive_payload(archive_path)
        parsed = parse_medium_archive(archive, include_drafts=include_drafts)
    except MediumArchiveReadError as error:
        return error.to_payload()
    except (OSError, UnicodeDecodeError, ValueError, zipfile.BadZipFile) as error:
        return MediumArchiveReadError("invalid_medium_export", archive_path, str(error)).to_payload()

    bootstrap_schema(repository.client)
    now = _now()
    counts = _counts()

    source = _source_document(now)
    counts["sources"] += int(repository.upsert("sources", source)["created"])

    raw = _raw_snapshot(archive, now)
    counts["raw_snapshots"] += int(repository.upsert("raw_snapshots", raw)["created"])

    import_run_key = stable_key(SOURCE_KEY, "archive", archive.manifest_sha256[:16], now[:10], prefix="import")
    import_run = {
        "_key": import_run_key,
        "started_at": now,
        "finished_at": None,
        "status": "running",
        "command": _command(archive, include_drafts=include_drafts),
        "source_key": SOURCE_KEY,
        "input_ref": archive.ref,
        "counts": {},
        "error": None,
        "metadata": {
            "archive": _archive_payload(archive),
            "include_drafts": include_drafts,
            "skipped": parsed.skipped,
        },
    }
    repository.upsert("import_runs", import_run)

    for item in parsed.items:
        counts = _ingest_item(repository, settings, item, raw, import_run_key, now, counts)

    import_run["finished_at"] = _now()
    import_run["status"] = "ok"
    import_run["counts"] = counts
    repository.upsert("import_runs", import_run)

    return {
        "status": "ok",
        "source_key": SOURCE_KEY,
        "import_run_key": import_run_key,
        "archive": _archive_payload(archive),
        "include_drafts": include_drafts,
        "created": counts,
        "deduplicated": {
            "documents": max(len(parsed.items) - counts["documents"], 0),
            "chunks": 0 if counts["chunks"] > 0 else _existing_chunk_count(repository, parsed.items),
        },
        "skipped": parsed.skipped,
    }


def read_medium_archive_payload(path: Path) -> MediumArchivePayload:
    archive_path = path.expanduser()
    if not archive_path.exists():
        raise MediumArchiveReadError("archive_not_readable", archive_path, "Archive path does not exist")
    if archive_path.is_dir():
        return _read_archive_directory(archive_path)
    if archive_path.is_file() and archive_path.suffix.lower() == ".zip":
        return _read_archive_zip(archive_path)
    raise MediumArchiveReadError("archive_not_readable", archive_path, "Archive must be a directory or .zip file")


def parse_medium_archive(archive: MediumArchivePayload, *, include_drafts: bool = False) -> ParsedSourceFeed:
    items: list[NormalizedSourceItem] = []
    skipped: list[dict[str, str]] = []
    for post in archive.posts:
        parsed = _parse_post(post, archive=archive, include_drafts=include_drafts)
        if isinstance(parsed, NormalizedSourceItem):
            items.append(parsed)
        else:
            skipped.append(parsed)
    return ParsedSourceFeed(
        title=DISPLAY_NAME,
        feed_url=None,
        media_type="application/vnd.medium.export+html",
        items=items,
        skipped=skipped,
    )


def medium_post_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urllib.parse.urlparse(url)
    if "medium.com" not in parsed.netloc:
        return None
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) >= 2 and parts[0] == "p":
        return parts[1]
    if not parts:
        return None
    match = POST_ID_RE.search(parts[-1])
    return match.group(1) if match else None


def canonical_id_from_post_id(post_id: str) -> str:
    return slugify(f"medium-post-{post_id}", fallback="medium-post")


def _read_archive_directory(path: Path) -> MediumArchivePayload:
    readme = _find_readme_in_directory(path)
    if readme is None:
        raise MediumArchiveReadError("invalid_medium_export", path, "README.html was not found")

    root = readme.parent
    files = sorted(candidate for candidate in root.rglob("*") if candidate.is_file())
    entries: list[dict[str, Any]] = []
    posts: list[MediumPostPayload] = []
    total_size = 0
    for file_path in files:
        data = file_path.read_bytes()
        relative = file_path.relative_to(root).as_posix()
        digest = sha256_text(data)
        size = len(data)
        total_size += size
        entries.append({"path": relative, "size_bytes": size, "sha256": digest})
        if _is_post_path(relative):
            posts.append(
                MediumPostPayload(
                    relative_path=relative,
                    payload=data.decode("utf-8", errors="replace"),
                    sha256=digest,
                    size_bytes=size,
                ),
            )

    if not posts:
        raise MediumArchiveReadError("posts_not_found", path, "No posts/*.html files were found")
    manifest_json = _manifest_json(entries)
    return MediumArchivePayload(
        kind="directory",
        ref=str(path),
        manifest_sha256=sha256_text(manifest_json),
        manifest_json=manifest_json,
        posts=posts,
        total_files=len(entries),
        total_size_bytes=total_size,
        root=str(root),
    )


def _read_archive_zip(path: Path) -> MediumArchivePayload:
    try:
        with zipfile.ZipFile(path) as archive:
            members = [info for info in archive.infolist() if not info.is_dir()]
            root_prefix = _find_zip_root_prefix(members)
            if root_prefix is None:
                raise MediumArchiveReadError("invalid_medium_export", path, "README.html was not found")

            entries: list[dict[str, Any]] = []
            posts: list[MediumPostPayload] = []
            total_size = 0
            for info in sorted(members, key=lambda item: item.filename):
                relative = _zip_relative_name(info.filename, root_prefix)
                if relative is None:
                    continue
                data = archive.read(info.filename)
                digest = sha256_text(data)
                size = len(data)
                total_size += size
                entries.append({"path": relative, "size_bytes": size, "sha256": digest})
                if _is_post_path(relative):
                    posts.append(
                        MediumPostPayload(
                            relative_path=relative,
                            payload=data.decode("utf-8", errors="replace"),
                            sha256=digest,
                            size_bytes=size,
                        ),
                    )
    except MediumArchiveReadError:
        raise
    except (OSError, zipfile.BadZipFile) as error:
        raise MediumArchiveReadError("archive_not_readable", path, str(error)) from error

    if not posts:
        raise MediumArchiveReadError("posts_not_found", path, "No posts/*.html files were found")
    manifest_json = _manifest_json(entries)
    return MediumArchivePayload(
        kind="zip",
        ref=str(path),
        manifest_sha256=sha256_text(manifest_json),
        manifest_json=manifest_json,
        posts=posts,
        total_files=len(entries),
        total_size_bytes=total_size,
        root=root_prefix or None,
    )


def _find_readme_in_directory(path: Path) -> Path | None:
    direct = path / README_NAME
    if direct.is_file():
        return direct
    candidates = sorted(
        (candidate for candidate in path.rglob(README_NAME) if candidate.is_file()),
        key=lambda candidate: (len(candidate.relative_to(path).parts), str(candidate)),
    )
    return candidates[0] if candidates else None


def _find_zip_root_prefix(members: list[zipfile.ZipInfo]) -> str | None:
    candidates = sorted(
        (info.filename for info in members if Path(info.filename).name == README_NAME),
        key=lambda name: (len(Path(name).parts), name),
    )
    if not candidates:
        return None
    parent = Path(candidates[0]).parent.as_posix()
    return "" if parent == "." else parent


def _zip_relative_name(name: str, root_prefix: str) -> str | None:
    if not root_prefix:
        return name
    prefix = root_prefix.rstrip("/") + "/"
    if not name.startswith(prefix):
        return None
    return name[len(prefix) :]


def _is_post_path(relative: str) -> bool:
    path = Path(relative)
    return len(path.parts) == 2 and path.parts[0] == "posts" and path.suffix.lower() == ".html"


def _manifest_json(entries: list[dict[str, Any]]) -> str:
    return json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_post(
    post: MediumPostPayload,
    *,
    archive: MediumArchivePayload,
    include_drafts: bool,
) -> NormalizedSourceItem | dict[str, str]:
    parser = _MediumPostHTMLParser()
    parser.feed(post.payload)
    parser.close()

    post_id = parser.post_id or medium_post_id_from_url(parser.canonical_url)
    guid = post_id or post.relative_path
    status = "draft" if Path(post.relative_path).name.startswith("draft_") or not parser.published_at else "published"
    if status == "draft" and not include_drafts:
        return {"guid": guid, "reason": "draft_excluded"}
    if not post_id:
        return {"guid": guid, "reason": "missing_post_id"}

    text = parser.text
    if not text:
        return {"guid": post_id, "reason": "empty_text"}

    title = parser.title or _title_from_text(text)
    export_date = _parse_export_date(post.payload)
    medium_post = {
        "post_id": post_id,
        "canonical_url": parser.canonical_url,
        "medium_url": parser.post_url or f"https://medium.com/p/{post_id}",
        "local_post_path": post.relative_path,
        "post_sha256": post.sha256,
        "size_bytes": post.size_bytes,
        "exported_at": export_date,
        "archive": _archive_payload(archive),
    }
    return NormalizedSourceItem(
        canonical_id=canonical_id_from_post_id(post_id),
        title=title,
        text=text,
        url=parser.canonical_url or parser.post_url or f"https://medium.com/p/{post_id}",
        guid=post_id,
        published_at=parser.published_at,
        language="unknown",
        author=parser.author,
        tags=[],
        metadata={
            "status": status,
            "medium_post": medium_post,
            "images": _dedupe_dicts(parser.images),
            "links": _dedupe_strings(parser.links),
        },
    )


def _ingest_item(
    repository: KnowledgeRepository,
    settings: Settings,
    item: NormalizedSourceItem,
    raw: dict[str, Any],
    import_run_key: str,
    now: str,
    counts: dict[str, int],
) -> dict[str, int]:
    doc_key = document_key(SOURCE_KEY, item.canonical_id)
    medium_post = {**item.metadata.get("medium_post", {}), "raw_snapshot_key": raw["_key"]}
    document = {
        "_key": doc_key,
        "source_key": SOURCE_KEY,
        "canonical_id": item.canonical_id,
        "title": item.title,
        "text": item.text,
        "language": item.language,
        "published_at": item.published_at,
        "url": item.url,
        "status": item.metadata.get("status", "published"),
        "metadata": {
            **item.metadata,
            "medium_post": medium_post,
            "tags": item.tags,
            "author": item.author,
            "raw_snapshot_key": raw["_key"],
        },
        "created_at": now,
        "updated_at": now,
    }
    counts["documents"] += int(repository.upsert("documents", document)["created"])
    counts["edges"] += int(
        repository.upsert_edge(
            "document_from_source",
            {
                "_key": stable_key(doc_key, SOURCE_KEY, prefix="edge"),
                "_from": f"documents/{doc_key}",
                "_to": f"sources/{SOURCE_KEY}",
                "import_run_key": import_run_key,
                "provenance": _provenance(item, raw),
                "created_at": now,
            },
        )["created"],
    )

    _upsert_author(repository, item, doc_key, raw, import_run_key, now, counts)
    _upsert_chunks(repository, settings, item, doc_key, raw, import_run_key, now, counts)
    return counts


def _upsert_author(
    repository: KnowledgeRepository,
    item: NormalizedSourceItem,
    doc_key: str,
    raw: dict[str, Any],
    import_run_key: str,
    now: str,
    counts: dict[str, int],
) -> None:
    if not item.author:
        return
    author_key = slugify(item.author, fallback="author")
    counts["authors"] += int(
        repository.upsert(
            "authors",
            {
                "_key": author_key,
                "display_name": item.author,
                "aliases": [],
                "metadata": {"source": "medium_export_author", "source_key": SOURCE_KEY},
            },
        )["created"],
    )
    counts["edges"] += int(
        repository.upsert_edge(
            "document_mentions_author",
            {
                "_key": stable_key(doc_key, author_key, prefix="edge"),
                "_from": f"documents/{doc_key}",
                "_to": f"authors/{author_key}",
                "confidence": 1.0,
                "method": "medium_export_author",
                "evidence": item.author,
                "import_run_key": import_run_key,
                "provenance": _provenance(item, raw),
                "created_at": now,
            },
        )["created"],
    )


def _upsert_chunks(
    repository: KnowledgeRepository,
    settings: Settings,
    item: NormalizedSourceItem,
    doc_key: str,
    raw: dict[str, Any],
    import_run_key: str,
    now: str,
    counts: dict[str, int],
) -> None:
    for chunk in split_text(item.text):
        c_key = chunk_key(doc_key, chunk.ordinal, chunk.text)
        counts["chunks"] += int(
            repository.upsert(
                "chunks",
                {
                    "_key": c_key,
                    "document_key": doc_key,
                    "ordinal": chunk.ordinal,
                    "text": chunk.text,
                    "token_count": chunk.token_count,
                    "char_start": chunk.char_start,
                    "char_end": chunk.char_end,
                    "embedding": hash_embedding(chunk.text, dimension=settings.embedding_dimension),
                    "embedding_model": HASH_EMBEDDING_MODEL,
                    "metadata": {
                        "source_key": SOURCE_KEY,
                        "raw_snapshot_key": raw["_key"],
                        "import_run_key": import_run_key,
                        "medium_post": item.metadata.get("medium_post"),
                    },
                },
            )["created"],
        )
        counts["edges"] += int(
            repository.upsert_edge(
                "chunk_of_document",
                {
                    "_key": stable_key(c_key, doc_key, prefix="edge"),
                    "_from": f"chunks/{c_key}",
                    "_to": f"documents/{doc_key}",
                    "ordinal": chunk.ordinal,
                    "created_at": now,
                },
            )["created"],
        )
        counts["edges"] += int(
            repository.upsert_edge(
                "chunk_derived_from_raw",
                {
                    "_key": stable_key(c_key, raw["_key"], prefix="edge"),
                    "_from": f"chunks/{c_key}",
                    "_to": f"raw_snapshots/{raw['_key']}",
                    "document_key": doc_key,
                    "char_start": chunk.char_start,
                    "char_end": chunk.char_end,
                    "import_run_key": import_run_key,
                },
            )["created"],
        )


def _source_document(now: str) -> dict[str, Any]:
    return {
        "_key": SOURCE_KEY,
        "type": "medium_export",
        "display_name": DISPLAY_NAME,
        "created_at": now,
        "metadata": {"platform": "medium", "source_kind": "account_export"},
    }


def _raw_snapshot(archive: MediumArchivePayload, now: str) -> dict[str, Any]:
    return {
        "_key": stable_key(SOURCE_KEY, archive.manifest_sha256, prefix="raw"),
        "source_key": SOURCE_KEY,
        "sha256": archive.manifest_sha256,
        "size_bytes": len(archive.manifest_json.encode("utf-8")),
        "media_type": "application/json",
        "storage_kind": "local_manifest",
        "storage_uri": archive.ref,
        "captured_at": now,
        "payload": archive.manifest_json,
        "metadata": {
            "archive": _archive_payload(archive),
            "safe_fixture": archive.ref.startswith("tests/fixtures/"),
            "payload_kind": "medium_export_manifest",
        },
    }


def _provenance(item: NormalizedSourceItem, raw: dict[str, Any]) -> dict[str, Any]:
    medium_post = {**item.metadata.get("medium_post", {}), "raw_snapshot_key": raw["_key"]}
    return {
        "url": item.url,
        "guid": item.guid,
        "raw_snapshot_key": raw["_key"],
        "source_key": SOURCE_KEY,
        "medium_post": medium_post,
    }


def _archive_payload(archive: MediumArchivePayload) -> dict[str, Any]:
    return {
        "kind": archive.kind,
        "ref": archive.ref,
        "manifest_sha256": archive.manifest_sha256,
        "total_files": archive.total_files,
        "total_size_bytes": archive.total_size_bytes,
        "root": archive.root,
    }


def _command(archive: MediumArchivePayload, *, include_drafts: bool) -> str:
    suffix = " --include-drafts" if include_drafts else ""
    return f"kb ingest medium-export --archive {archive.ref}{suffix}"


def _counts() -> dict[str, int]:
    return {
        "sources": 0,
        "raw_snapshots": 0,
        "documents": 0,
        "chunks": 0,
        "topics": 0,
        "authors": 0,
        "works": 0,
        "edges": 0,
    }


def _existing_chunk_count(repository: KnowledgeRepository, items: list[NormalizedSourceItem]) -> int:
    count = 0
    for item in items:
        doc_key = document_key(SOURCE_KEY, item.canonical_id)
        result = repository.client.aql(
            "RETURN LENGTH(FOR chunk IN chunks FILTER chunk.document_key == @doc RETURN 1)",
            {"doc": doc_key},
        )
        count += int(result[0])
    return count


def _title_from_text(text: str, *, max_length: int = 100) -> str:
    first = text.split(".", 1)[0].strip() or "Medium post"
    return first if len(first) <= max_length else first[: max_length - 3].rstrip() + "..."


def _parse_export_date(payload: str) -> str | None:
    match = EXPORT_DATE_RE.search(payload)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%B %d, %Y").date().isoformat()
    except ValueError:
        return match.group(1)


def _parse_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _dedupe_dicts(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for value in values:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
