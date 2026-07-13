from __future__ import annotations

import json
import re
import urllib.error
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from knowledge_base.config import Settings
from knowledge_base.ids import sha256_file, sha256_stream, sha256_text, slugify, stable_key, topic_key
from knowledge_base.net import UnsafeUrlError, open_public_url
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.schema import bootstrap_schema
from knowledge_base.sources.contracts import NormalizedSourceItem, ParsedSourceFeed
from knowledge_base.sources.ingest_core import (
    finalize_import_run,
    empty_counts,
    parse_date,
    planned_chunk_count,
    upsert_chunks,
    upsert_document,
    upsert_topics,
    utc_now,
)

SOURCE_KEY = "book-cube"
DISPLAY_NAME = "Книжный куб"
CHANNEL_URL = "https://t.me/book_cube"
DEFAULT_PUBLIC_URL = "https://t.me/s/book_cube"
LIVE_FETCH_HINT = "Save Telegram HTML/JSON export under data/raw/book-cube/ and rerun with --input."
ARCHIVE_HINT = "Export Telegram channel as machine-readable JSON and rerun with --archive."
HASHTAG_RE = re.compile(r"(?<!\w)#([\wа-яА-ЯёЁ_]+)")
ATTACHMENT_FIELDS = ("photo", "file", "thumbnail")
VOID_TAGS = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"}


@dataclass(frozen=True)
class SnapshotPayload:
    kind: str
    ref: str
    payload: str
    sha256: str
    media_type: str
    storage_kind: str


@dataclass(frozen=True)
class ArchivePayload:
    kind: str
    ref: str
    result_json: str
    result_sha256: str
    manifest_sha256: str
    snapshot: SnapshotPayload
    root_path: Path | None = None
    zip_members: dict[str, int] | None = None


class LiveFetchUnavailable(RuntimeError):
    def __init__(self, url: str, reason: str) -> None:
        super().__init__(reason)
        self.url = url
        self.reason = reason

    def to_payload(self, url: str | None = None) -> dict[str, Any]:
        return {
            "status": "error",
            "error": "live_fetch_unavailable",
            "source_key": SOURCE_KEY,
            "url": url or self.url,
            "hint": LIVE_FETCH_HINT,
            "reason": self.reason,
        }


class ArchiveReadError(RuntimeError):
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


class _TelegramHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.messages: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        self._message_depth = 0
        self._collect_text = False
        self._text_depth = 0
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        classes = set((attr.get("class") or "").split())
        if tag == "div" and "tgme_widget_message" in classes and attr.get("data-post"):
            self._current = {"data_post": attr["data-post"], "url": None, "published_at": None, "text": ""}
            self._message_depth = 1
            return

        if self._current is not None:
            # Void tags (<br>, <img>, ...) never emit an end tag; counting them would leave the
            # depth permanently unbalanced and drop the message and its text (finding #22).
            if tag not in VOID_TAGS:
                self._message_depth += 1
            if tag == "a" and "tgme_widget_message_date" in classes and attr.get("href"):
                self._current["url"] = attr["href"]
            if tag == "time" and attr.get("datetime"):
                self._current["published_at"] = parse_date(attr["datetime"])
            if tag == "div" and "tgme_widget_message_text" in classes:
                self._collect_text = True
                self._text_depth = 1
                self._text_parts = []
            elif self._collect_text:
                if tag not in VOID_TAGS:
                    self._text_depth += 1
                if tag in {"br", "p", "div"}:
                    self._text_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._collect_text:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._current is None or tag in VOID_TAGS:
            return
        if self._collect_text:
            if tag in {"p", "div"}:
                self._text_parts.append(" ")
            self._text_depth -= 1
            if self._text_depth == 0:
                self._current["text"] = _clean_text("".join(self._text_parts))
                self._collect_text = False
        self._message_depth -= 1
        if self._message_depth == 0:
            self.messages.append(self._current)
            self._current = None


def ingest_book_cube(
    repository: KnowledgeRepository,
    settings: Settings,
    *,
    input_path: Path | None = None,
    url: str = DEFAULT_PUBLIC_URL,
) -> dict[str, Any]:
    try:
        snapshot = read_snapshot_payload(input_path=input_path, url=url)
    except LiveFetchUnavailable as error:
        return error.to_payload(url)

    parsed = parse_snapshot(snapshot.payload, media_type=snapshot.media_type)
    bootstrap_schema(repository.client, embedding_dimension=settings.embedding_dimension)
    now = utc_now()
    counts = empty_counts()

    source = _source_document(now, url)
    counts["sources"] += int(repository.upsert("sources", source)["created"])

    raw = _raw_snapshot(snapshot, now)
    counts["raw_snapshots"] += int(repository.upsert("raw_snapshots", raw)["created"])

    import_run_key = stable_key(SOURCE_KEY, snapshot.kind, snapshot.sha256[:16], now[:10], prefix="import")
    import_run: dict[str, Any] = {
        "_key": import_run_key,
        "started_at": now,
        "finished_at": None,
        "status": "running",
        "command": _command(snapshot, url),
        "source_key": SOURCE_KEY,
        "input_ref": snapshot.ref,
        "counts": {},
        "error": None,
        "metadata": {"input": _input_payload(snapshot), "skipped": parsed.skipped},
    }
    repository.upsert("import_runs", import_run)

    failure: Exception | None = None
    try:
        for item in parsed.items:
            counts = _ingest_item(repository, settings, item, raw, import_run_key, now, counts)
        finalize_import_run(repository, import_run, status="ok", counts=counts)
    except Exception as exc:
        failure = exc
        raise
    finally:
        if failure is not None and import_run["status"] == "running":
            finalize_import_run(
                repository,
                import_run,
                status="error",
                counts=counts,
                error=f"{type(failure).__name__}: {failure}",
            )

    return {
        "status": "ok",
        "source_key": SOURCE_KEY,
        "import_run_key": import_run_key,
        "input": _input_payload(snapshot),
        "created": counts,
        "deduplicated": {
            "documents": max(len(parsed.items) - counts["documents"], 0),
            "chunks": max(planned_chunk_count(parsed.items) - counts["chunks"], 0),
        },
        "skipped": parsed.skipped,
    }


def ingest_book_cube_archive(
    repository: KnowledgeRepository,
    settings: Settings,
    *,
    archive_path: Path,
) -> dict[str, Any]:
    try:
        archive = read_archive_payload(archive_path)
    except ArchiveReadError as error:
        return error.to_payload()

    try:
        parsed = parse_snapshot(archive.snapshot.payload, media_type="application/json", archive=archive)
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        return ArchiveReadError("invalid_telegram_export", archive_path, str(error)).to_payload()

    bootstrap_schema(repository.client, embedding_dimension=settings.embedding_dimension)
    now = utc_now()
    counts = empty_counts()

    source = _source_document(now, DEFAULT_PUBLIC_URL)
    counts["sources"] += int(repository.upsert("sources", source)["created"])

    raw = _raw_snapshot(archive.snapshot, now, archive=archive)
    counts["raw_snapshots"] += int(repository.upsert("raw_snapshots", raw)["created"])

    import_run_key = stable_key(SOURCE_KEY, "archive", archive.manifest_sha256[:16], now[:10], prefix="import")
    import_run: dict[str, Any] = {
        "_key": import_run_key,
        "started_at": now,
        "finished_at": None,
        "status": "running",
        "command": f"kb ingest book-cube-archive --archive {archive.ref}",
        "source_key": SOURCE_KEY,
        "input_ref": archive.ref,
        "counts": {},
        "error": None,
        "metadata": {"archive": _archive_payload(archive), "skipped": parsed.skipped},
    }
    repository.upsert("import_runs", import_run)

    failure: Exception | None = None
    try:
        for item in parsed.items:
            counts = _ingest_item(repository, settings, item, raw, import_run_key, now, counts)
        finalize_import_run(repository, import_run, status="ok", counts=counts)
    except Exception as exc:
        failure = exc
        raise
    finally:
        if failure is not None and import_run["status"] == "running":
            finalize_import_run(
                repository,
                import_run,
                status="error",
                counts=counts,
                error=f"{type(failure).__name__}: {failure}",
            )

    return {
        "status": "ok",
        "source_key": SOURCE_KEY,
        "import_run_key": import_run_key,
        "archive": _archive_payload(archive),
        "created": counts,
        "deduplicated": {
            "documents": max(len(parsed.items) - counts["documents"], 0),
            "chunks": max(planned_chunk_count(parsed.items) - counts["chunks"], 0),
        },
        "skipped": parsed.skipped,
    }


def read_snapshot_payload(*, input_path: Path | None, url: str) -> SnapshotPayload:
    if input_path is not None:
        payload = input_path.read_text(encoding="utf-8")
        return SnapshotPayload(
            kind="file",
            ref=str(input_path),
            payload=payload,
            sha256=sha256_text(payload),
            media_type=detect_media_type(input_path, payload),
            storage_kind="local_file",
        )

    payload = fetch_snapshot_payload(url)
    return SnapshotPayload(
        kind="url",
        ref=url,
        payload=payload,
        sha256=sha256_text(payload),
        media_type=detect_media_type(None, payload),
        storage_kind="inline",
    )


def read_archive_payload(path: Path) -> ArchivePayload:
    archive_path = path.expanduser()
    if not archive_path.exists():
        raise ArchiveReadError("archive_not_readable", archive_path, "Archive path does not exist")
    if archive_path.is_dir():
        return _read_archive_directory(archive_path)
    if archive_path.is_file() and archive_path.suffix.lower() == ".zip":
        return _read_archive_zip(archive_path)
    raise ArchiveReadError("archive_not_readable", archive_path, "Archive must be a directory or .zip file")


def _read_archive_directory(path: Path) -> ArchivePayload:
    result_path = _find_result_json_in_directory(path)
    if result_path is None:
        raise ArchiveReadError("result_json_not_found", path)
    try:
        payload = result_path.read_text(encoding="utf-8")
    except OSError as error:
        raise ArchiveReadError("archive_not_readable", path, str(error)) from error

    result_sha = sha256_text(payload)
    snapshot = SnapshotPayload(
        kind="directory",
        ref=str(path),
        payload=payload,
        sha256=result_sha,
        media_type="application/json",
        storage_kind="local_file",
    )
    return ArchivePayload(
        kind="directory",
        ref=str(path),
        result_json=str(result_path),
        result_sha256=result_sha,
        manifest_sha256=_directory_manifest_sha256(path),
        snapshot=snapshot,
        root_path=result_path.parent,
    )


def _read_archive_zip(path: Path) -> ArchivePayload:
    try:
        with zipfile.ZipFile(path) as archive:
            members = [info for info in archive.infolist() if not info.is_dir()]
            result_name = _find_result_json_in_zip(members)
            if result_name is None:
                raise ArchiveReadError("result_json_not_found", path)
            payload = archive.read(result_name).decode("utf-8", errors="replace")
            member_hashes = {}
            for info in members:
                with archive.open(info) as handle:
                    member_hashes[info.filename] = sha256_stream(handle)
    except ArchiveReadError:
        raise
    except (OSError, zipfile.BadZipFile) as error:
        raise ArchiveReadError("archive_not_readable", path, str(error)) from error

    result_sha = sha256_text(payload)
    zip_members = {info.filename: info.file_size for info in members}
    snapshot = SnapshotPayload(
        kind="zip",
        ref=str(path),
        payload=payload,
        sha256=result_sha,
        media_type="application/json",
        storage_kind="local_file",
    )
    return ArchivePayload(
        kind="zip",
        ref=str(path),
        result_json=result_name,
        result_sha256=result_sha,
        manifest_sha256=_zip_manifest_sha256(members, member_hashes),
        snapshot=snapshot,
        zip_members=zip_members,
    )


def _find_result_json_in_directory(path: Path) -> Path | None:
    direct = path / "result.json"
    if direct.is_file():
        return direct
    candidates = sorted(
        (candidate for candidate in path.rglob("result.json") if candidate.is_file()),
        key=lambda candidate: (len(candidate.relative_to(path).parts), str(candidate)),
    )
    return candidates[0] if candidates else None


def _find_result_json_in_zip(members: list[zipfile.ZipInfo]) -> str | None:
    names = [info.filename for info in members]
    if "result.json" in names:
        return "result.json"
    candidates = sorted(
        (name for name in names if Path(name).name == "result.json"),
        key=lambda name: (len(Path(name).parts), name),
    )
    return candidates[0] if candidates else None


def _directory_manifest_sha256(path: Path) -> str:
    # Content-hash every file (streaming, so large media is not loaded into memory) so a
    # same-size replacement changes the manifest and is not treated as unchanged (finding #24).
    entries = [
        {
            "path": file_path.relative_to(path).as_posix(),
            "size_bytes": file_path.stat().st_size,
            "sha256": sha256_file(file_path),
        }
        for file_path in sorted(candidate for candidate in path.rglob("*") if candidate.is_file())
    ]
    return sha256_text(json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _zip_manifest_sha256(members: list[zipfile.ZipInfo], member_hashes: dict[str, str]) -> str:
    entries = [
        {"path": info.filename, "size_bytes": info.file_size, "sha256": member_hashes[info.filename]}
        for info in sorted(members, key=lambda item: item.filename)
    ]
    return sha256_text(json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def fetch_snapshot_payload(url: str, *, timeout_seconds: float = 15.0) -> str:
    headers = {
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        "User-Agent": "knowledge-base-ingest/0.1 (+https://t.me/book_cube)",
    }
    try:
        with open_public_url(url, headers=headers, timeout=timeout_seconds) as response:
            status = getattr(response, "status", 200)
            if status >= 400:
                raise LiveFetchUnavailable(url, f"HTTP {status}")
            return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
    except UnsafeUrlError as error:
        raise LiveFetchUnavailable(url, f"blocked URL: {error}") from error
    except urllib.error.HTTPError as error:
        raise LiveFetchUnavailable(url, f"HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise LiveFetchUnavailable(url, str(error)) from error


def parse_snapshot(payload: str, *, media_type: str, archive: ArchivePayload | None = None) -> ParsedSourceFeed:
    if media_type == "application/json" or payload.lstrip().startswith("{"):
        return _parse_json_export(payload, archive=archive)
    return _parse_public_html(payload)


def detect_media_type(input_path: Path | None, payload: str) -> str:
    if input_path is not None and input_path.suffix.lower() == ".json":
        return "application/json"
    if payload.lstrip().startswith("{"):
        return "application/json"
    return "text/html"


def canonical_id_from_post(data_post: str | None, message_id: int | str | None) -> str:
    if data_post:
        return slugify(data_post.replace("/", "-"), fallback="telegram-message")
    return slugify(f"book_cube-{message_id}", fallback="telegram-message")


def title_from_text(text: str, *, max_length: int = 80) -> str:
    # Callers pass whitespace-collapsed text (no newlines), so the title is the leading
    # sentence of the post (finding #38: the previous splitlines() first-line logic was dead).
    stripped = text.strip() or "Книжный куб"
    title = re.split(r"(?<=[.!?])\s+", stripped, maxsplit=1)[0] or stripped
    if len(title) <= max_length:
        return title
    return title[: max_length - 3].rstrip() + "..."


def _parse_public_html(payload: str) -> ParsedSourceFeed:
    parser = _TelegramHTMLParser()
    parser.feed(payload)
    items: list[NormalizedSourceItem] = []
    skipped: list[dict[str, str]] = []
    for message in parser.messages:
        data_post = str(message.get("data_post") or "")
        text = _clean_text(message.get("text"))
        if not text:
            skipped.append({"guid": data_post, "reason": "empty_text"})
            continue
        message_id = data_post.rsplit("/", 1)[-1] if "/" in data_post else data_post
        tags = _hashtags(text)
        canonical_id = canonical_id_from_post(data_post, message_id)
        items.append(
            NormalizedSourceItem(
                canonical_id=canonical_id,
                title=title_from_text(text),
                text=text,
                url=message.get("url") or f"{CHANNEL_URL}/{message_id}",
                guid=data_post,
                published_at=message.get("published_at"),
                language="unknown",
                author=None,
                tags=tags,
                metadata={"message_id": message_id, "data_post": data_post, "snapshot_type": "telegram_html"},
            ),
        )
    return ParsedSourceFeed(
        title=DISPLAY_NAME,
        feed_url=DEFAULT_PUBLIC_URL,
        media_type="text/html",
        items=items,
        skipped=skipped,
    )


def _parse_json_export(payload: str, *, archive: ArchivePayload | None = None) -> ParsedSourceFeed:
    data = json.loads(payload)
    items: list[NormalizedSourceItem] = []
    skipped: list[dict[str, str]] = []
    for message in data.get("messages", []):
        message_id = message.get("id")
        guid = str(message_id)
        if message.get("type") != "message":
            skipped.append({"guid": guid, "reason": "unsupported_type"})
            continue
        text = _json_message_text(message)
        if not text:
            skipped.append({"guid": guid, "reason": "empty_text"})
            continue
        tags = _hashtags(text)
        canonical_id = canonical_id_from_post(None, message_id)
        attachments = collect_attachment_refs(message, archive=archive)
        metadata = {
            "message_id": message_id,
            "snapshot_type": "telegram_json",
            "attachments": attachments,
        }
        if archive is not None:
            metadata["archive"] = _archive_payload(archive)
        items.append(
            NormalizedSourceItem(
                canonical_id=canonical_id,
                title=title_from_text(text),
                text=text,
                url=f"{CHANNEL_URL}/{message_id}",
                guid=guid,
                published_at=parse_date(message.get("date")),
                language="unknown",
                author=None,
                tags=tags,
                metadata=metadata,
            ),
        )
    return ParsedSourceFeed(
        title=data.get("name") or DISPLAY_NAME,
        feed_url=DEFAULT_PUBLIC_URL,
        media_type="application/json",
        items=items,
        skipped=skipped,
    )


def _json_message_text(message: dict[str, Any]) -> str:
    body = _rich_text_value(message.get("text_entities") if "text_entities" in message else message.get("text", ""))
    caption = _rich_text_value(
        message.get("caption_entities") if "caption_entities" in message else message.get("caption", ""),
    )
    return _clean_text(" ".join(part for part in (body, caption) if part))


def _rich_text_value(value: Any) -> str:
    if isinstance(value, list):
        parts = [part.get("text", "") if isinstance(part, dict) else str(part) for part in value]
        return _clean_text("".join(parts))
    return _clean_text(str(value or ""))


def collect_attachment_refs(message: dict[str, Any], *, archive: ArchivePayload | None) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for field in ATTACHMENT_FIELDS:
        relative_path = _attachment_relative_path(message.get(field))
        if relative_path is None:
            continue
        mime_type = str(message.get("mime_type") or "").strip() or None
        media_type = str(message.get("media_type") or "").strip() or _guess_media_type(field, relative_path, mime_type)
        local_path = _attachment_local_path(relative_path, archive)
        refs.append(
            {
                "field": field,
                "relative_path": relative_path,
                "local_path": local_path,
                "media_type": media_type,
                "mime_type": mime_type,
                "size_bytes": _attachment_size(relative_path, field, message, archive),
            },
        )
    return refs


def _attachment_relative_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or stripped.startswith("("):
        return None
    normalized = stripped.replace("\\", "/")
    # Attachment paths come from an attacker-controllable result.json. Reject absolute
    # paths, Windows drive/UNC paths, and ".." traversal so they cannot be joined with the
    # archive root to stat() or record files outside the export (finding #41).
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(normalized)
    if posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts:
        return None
    return normalized


def _attachment_local_path(relative_path: str, archive: ArchivePayload | None) -> str | None:
    if archive is None or archive.root_path is None:
        return None
    return str(archive.root_path / relative_path)


def _attachment_size(
    relative_path: str,
    field: str,
    message: dict[str, Any],
    archive: ArchivePayload | None,
) -> int | None:
    if archive is not None and archive.root_path is not None:
        candidate = archive.root_path / relative_path
        if candidate.is_file():
            return candidate.stat().st_size
    if archive is not None and archive.zip_members is not None:
        prefixed = _zip_prefixed_path(relative_path, archive)
        for name in (relative_path, prefixed):
            if name and name in archive.zip_members:
                return archive.zip_members[name]
    for size_key in (f"{field}_file_size", "file_size", "size"):
        value = message.get(size_key)
        if isinstance(value, int):
            return value
    return None


def _zip_prefixed_path(relative_path: str, archive: ArchivePayload) -> str | None:
    result_parent = Path(archive.result_json).parent
    if str(result_parent) == ".":
        return None
    return (result_parent / relative_path).as_posix()


def _guess_media_type(field: str, relative_path: str, mime_type: str | None) -> str:
    if mime_type:
        return mime_type.split("/", 1)[0]
    suffix = Path(relative_path).suffix.lower()
    if field == "photo" or suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return "photo"
    if suffix in {".mp4", ".mov", ".m4v", ".webm"}:
        return "video_file"
    if suffix in {".mp3", ".m4a", ".ogg", ".wav"}:
        return "audio_file"
    return "file"


def _ingest_item(
    repository: KnowledgeRepository,
    settings: Settings,
    item: NormalizedSourceItem,
    raw: dict[str, Any],
    import_run_key: str,
    now: str,
    counts: dict[str, int],
) -> dict[str, int]:
    provenance = _provenance(item, raw)
    metadata = {**item.metadata, "tags": item.tags, "raw_snapshot_key": raw["_key"]}
    doc_key = upsert_document(
        repository,
        SOURCE_KEY,
        item,
        import_run_key,
        now,
        counts,
        metadata=metadata,
        status="published",
        provenance=provenance,
    )
    upsert_topics(
        repository,
        item,
        doc_key,
        SOURCE_KEY,
        import_run_key,
        now,
        counts,
        method="telegram_hashtag",
        evidence=lambda tag: f"#{tag}",
        provenance=provenance,
    )
    upsert_chunks(
        repository,
        settings,
        item,
        doc_key,
        raw,
        import_run_key,
        now,
        counts,
        chunk_metadata={
            "source_key": SOURCE_KEY,
            "tags": item.tags,
            "raw_snapshot_key": raw["_key"],
            "import_run_key": import_run_key,
            "message_id": item.metadata.get("message_id"),
            "archive": item.metadata.get("archive"),
            "attachments": item.metadata.get("attachments", []),
        },
        topic_method="telegram_hashtag",
        topic_evidence=lambda tag: f"#{tag}",
        provenance=provenance,
    )
    return counts


def _source_document(now: str, url: str) -> dict[str, Any]:
    return {
        "_key": SOURCE_KEY,
        "type": "telegram_channel",
        "display_name": DISPLAY_NAME,
        "created_at": now,
        "metadata": {
            "channel_url": CHANNEL_URL,
            "public_preview_url": url,
            "platform": "telegram_public_channel",
        },
    }


def _raw_snapshot(snapshot: SnapshotPayload, now: str, *, archive: ArchivePayload | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "input_kind": snapshot.kind,
        "safe_fixture": snapshot.ref.startswith("tests/fixtures/"),
    }
    if archive is not None:
        metadata["archive"] = _archive_payload(archive)
    return {
        "_key": stable_key(SOURCE_KEY, snapshot.sha256, prefix="raw"),
        "source_key": SOURCE_KEY,
        "sha256": snapshot.sha256,
        "size_bytes": len(snapshot.payload.encode("utf-8")),
        "media_type": snapshot.media_type,
        "storage_kind": snapshot.storage_kind,
        "storage_uri": snapshot.ref,
        "captured_at": now,
        "payload": snapshot.payload,
        "metadata": metadata,
    }


def _provenance(item: NormalizedSourceItem, raw: dict[str, Any]) -> dict[str, Any]:
    telegram_message = {
        "canonical_id": item.canonical_id,
        "title": item.title,
        "published_at": item.published_at,
        "message_id": item.metadata.get("message_id"),
    }
    if "archive" in item.metadata:
        telegram_message["archive"] = item.metadata["archive"]
    if item.metadata.get("attachments"):
        telegram_message["attachments"] = item.metadata["attachments"]
    return {
        "url": item.url,
        "guid": item.guid,
        "raw_snapshot_key": raw["_key"],
        "source_key": SOURCE_KEY,
        "telegram_message": telegram_message,
    }


def _input_payload(snapshot: SnapshotPayload) -> dict[str, str]:
    return {"kind": snapshot.kind, "ref": snapshot.ref, "sha256": snapshot.sha256}


def _archive_payload(archive: ArchivePayload) -> dict[str, str]:
    return {
        "kind": archive.kind,
        "ref": archive.ref,
        "result_json": archive.result_json,
        "result_sha256": archive.result_sha256,
        "manifest_sha256": archive.manifest_sha256,
    }


def _command(snapshot: SnapshotPayload, url: str) -> str:
    if snapshot.kind == "file":
        return f"kb ingest book-cube --input {snapshot.ref}"
    return f"kb ingest book-cube --url {url}"


def _hashtags(text: str) -> list[str]:
    seen: set[str] = set()
    tags: list[str] = []
    for match in HASHTAG_RE.finditer(text):
        label = match.group(1)
        key = topic_key(label)
        if key not in seen:
            seen.add(key)
            tags.append(label)
    return tags


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())
