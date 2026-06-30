from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
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


SOURCE_KEY = "book-cube"
DISPLAY_NAME = "Книжный куб"
CHANNEL_URL = "https://t.me/book_cube"
DEFAULT_PUBLIC_URL = "https://t.me/s/book_cube"
LIVE_FETCH_HINT = "Save Telegram HTML/JSON export under data/raw/book-cube/ and rerun with --input."
HASHTAG_RE = re.compile(r"(?<!\w)#([\wа-яА-ЯёЁ_]+)")


@dataclass(frozen=True)
class SnapshotPayload:
    kind: str
    ref: str
    payload: str
    sha256: str
    media_type: str
    storage_kind: str


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
            self._message_depth += 1
            if tag == "a" and "tgme_widget_message_date" in classes and attr.get("href"):
                self._current["url"] = attr["href"]
            if tag == "time" and attr.get("datetime"):
                self._current["published_at"] = _parse_date(attr["datetime"])
            if tag == "div" and "tgme_widget_message_text" in classes:
                self._collect_text = True
                self._text_depth = 1
                self._text_parts = []
            elif self._collect_text:
                self._text_depth += 1
                if tag in {"br", "p", "div"}:
                    self._text_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._collect_text:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._current is None:
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
    bootstrap_schema(repository.client)
    now = _now()
    counts = _counts()

    source = _source_document(now, url)
    counts["sources"] += int(repository.upsert("sources", source)["created"])

    raw = _raw_snapshot(snapshot, now)
    counts["raw_snapshots"] += int(repository.upsert("raw_snapshots", raw)["created"])

    import_run_key = stable_key(SOURCE_KEY, snapshot.kind, snapshot.sha256[:16], now[:10], prefix="import")
    import_run = {
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
        "input": _input_payload(snapshot),
        "created": counts,
        "deduplicated": {
            "documents": max(len(parsed.items) - counts["documents"], 0),
            "chunks": 0 if counts["chunks"] > 0 else _existing_chunk_count(repository, parsed.items),
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


def fetch_snapshot_payload(url: str, *, timeout_seconds: float = 15.0) -> str:
    request = urllib.request.Request(url, method="GET")
    request.add_header("Accept", "text/html,application/json;q=0.9,*/*;q=0.8")
    request.add_header("User-Agent", "knowledge-base-ingest/0.1 (+https://t.me/book_cube)")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            status = getattr(response, "status", 200)
            if status >= 400:
                raise LiveFetchUnavailable(url, f"HTTP {status}")
            return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        raise LiveFetchUnavailable(url, f"HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise LiveFetchUnavailable(url, str(error)) from error


def parse_snapshot(payload: str, *, media_type: str) -> ParsedSourceFeed:
    if media_type == "application/json" or payload.lstrip().startswith("{"):
        return _parse_json_export(payload)
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
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    title = first_line or "Книжный куб"
    sentence = re.split(r"(?<=[.!?])\s+", title, maxsplit=1)[0]
    if sentence:
        title = sentence
    if len(title) <= max_length:
        return title
    return title[: max_length - 3].rstrip() + "..."


def topic_key(label: str) -> str:
    slug = re.sub(r"[^a-zA-Zа-яА-ЯёЁ0-9_-]+", "-", label.lstrip("#").strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-_")
    return slug or "topic"


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


def _parse_json_export(payload: str) -> ParsedSourceFeed:
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
        items.append(
            NormalizedSourceItem(
                canonical_id=canonical_id,
                title=title_from_text(text),
                text=text,
                url=f"{CHANNEL_URL}/{message_id}",
                guid=guid,
                published_at=_parse_date(message.get("date")),
                language="unknown",
                author=None,
                tags=tags,
                metadata={"message_id": message_id, "snapshot_type": "telegram_json"},
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
    if "text_entities" in message:
        parts = [entity.get("text", "") if isinstance(entity, dict) else str(entity) for entity in message["text_entities"]]
        return _clean_text("".join(parts))
    text = message.get("text", "")
    if isinstance(text, list):
        parts = [part.get("text", "") if isinstance(part, dict) else str(part) for part in text]
        return _clean_text("".join(parts))
    return _clean_text(str(text))


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
    document = {
        "_key": doc_key,
        "source_key": SOURCE_KEY,
        "canonical_id": item.canonical_id,
        "title": item.title,
        "text": item.text,
        "language": item.language,
        "published_at": item.published_at,
        "url": item.url,
        "status": "published",
        "metadata": {**item.metadata, "tags": item.tags, "raw_snapshot_key": raw["_key"]},
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
    _upsert_topics(repository, item, doc_key, raw, import_run_key, now, counts)
    _upsert_chunks(repository, settings, item, doc_key, raw, import_run_key, now, counts)
    return counts


def _upsert_topics(
    repository: KnowledgeRepository,
    item: NormalizedSourceItem,
    doc_key: str,
    raw: dict[str, Any],
    import_run_key: str,
    now: str,
    counts: dict[str, int],
) -> None:
    for tag in item.tags:
        key = topic_key(tag)
        counts["topics"] += int(
            repository.upsert(
                "topics",
                {
                    "_key": key,
                    "label": tag,
                    "language": "unknown",
                    "description": "",
                    "confidence": 1.0,
                    "metadata": {"source": "telegram_hashtag", "source_key": SOURCE_KEY},
                },
            )["created"],
        )
        counts["edges"] += int(
            repository.upsert_edge(
                "document_mentions_topic",
                {
                    "_key": stable_key(doc_key, key, prefix="edge"),
                    "_from": f"documents/{doc_key}",
                    "_to": f"topics/{key}",
                    "confidence": 1.0,
                    "method": "telegram_hashtag",
                    "evidence": f"#{tag}",
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
                    "metadata": {"source_key": SOURCE_KEY, "tags": item.tags},
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
        for tag in item.tags:
            key = topic_key(tag)
            counts["edges"] += int(
                repository.upsert_edge(
                    "document_mentions_topic",
                    {
                        "_key": stable_key(c_key, key, prefix="edge"),
                        "_from": f"chunks/{c_key}",
                        "_to": f"topics/{key}",
                        "confidence": 1.0,
                        "method": "telegram_hashtag",
                        "evidence": f"#{tag}",
                        "import_run_key": import_run_key,
                        "provenance": _provenance(item, raw),
                        "created_at": now,
                    },
                )["created"],
            )


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


def _raw_snapshot(snapshot: SnapshotPayload, now: str) -> dict[str, Any]:
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
        "metadata": {"input_kind": snapshot.kind, "safe_fixture": snapshot.ref.startswith("tests/fixtures/")},
    }


def _provenance(item: NormalizedSourceItem, raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": item.url,
        "guid": item.guid,
        "raw_snapshot_key": raw["_key"],
        "source_key": SOURCE_KEY,
        "telegram_message": {
            "canonical_id": item.canonical_id,
            "title": item.title,
            "published_at": item.published_at,
            "message_id": item.metadata.get("message_id"),
        },
    }


def _input_payload(snapshot: SnapshotPayload) -> dict[str, str]:
    return {"kind": snapshot.kind, "ref": snapshot.ref, "sha256": snapshot.sha256}


def _command(snapshot: SnapshotPayload, url: str) -> str:
    if snapshot.kind == "file":
        return f"kb ingest book-cube --input {snapshot.ref}"
    return f"kb ingest book-cube --url {url}"


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


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
