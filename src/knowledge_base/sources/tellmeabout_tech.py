from __future__ import annotations

import urllib.error
import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from knowledge_base.config import Settings
from knowledge_base.ids import sha256_text, slugify, stable_key
from knowledge_base.net import UnsafeUrlError, open_public_url
from knowledge_base.repository import KnowledgeRepository
from knowledge_base.schema import bootstrap_schema
from knowledge_base.sources.contracts import NormalizedSourceItem, ParsedSourceFeed
from knowledge_base.sources.ingest_core import (
    finalize_import_run,
    empty_counts,
    parse_date,
    planned_chunk_count,
    upsert_author,
    upsert_chunks,
    upsert_document,
    upsert_topics,
    utc_now,
)

SOURCE_KEY = "tellmeabout-tech"
DISPLAY_NAME = "Tell Me About Tech"
SITE_URL = "https://tellmeabout.tech/"
DEFAULT_FEED_URL = "https://tellmeabout.tech/feed"
LIVE_FETCH_HINT = "Save RSS/Medium export under data/raw/tellmeabout-tech/ and rerun with --input."


@dataclass(frozen=True)
class FeedPayload:
    kind: str
    ref: str
    payload: str
    sha256: str
    media_type: str
    storage_kind: str


class LiveFetchUnavailable(RuntimeError):
    def __init__(self, feed_url: str, reason: str) -> None:
        super().__init__(reason)
        self.feed_url = feed_url
        self.reason = reason

    def to_payload(self, feed_url: str | None = None) -> dict[str, Any]:
        return {
            "status": "error",
            "error": "live_fetch_unavailable",
            "source_key": SOURCE_KEY,
            "feed_url": feed_url or self.feed_url,
            "hint": LIVE_FETCH_HINT,
            "reason": self.reason,
        }


class FeedParseError(ValueError):
    """Raised when a fetched/loaded feed is not valid RSS/Atom (finding #6)."""

    def __init__(self, reason: str, feed_ref: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.feed_ref = feed_ref

    def to_payload(self, feed_ref: str | None = None) -> dict[str, Any]:
        return {
            "status": "error",
            "error": "invalid_feed",
            "source_key": SOURCE_KEY,
            "feed_ref": feed_ref or self.feed_ref,
            "hint": LIVE_FETCH_HINT,
            "reason": self.reason,
        }


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"p", "br", "div", "article", "section", "h1", "h2", "h3", "li"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"p", "div", "article", "section", "h1", "h2", "h3", "li"}:
            self.parts.append(" ")

    def text(self) -> str:
        return " ".join("".join(self.parts).split())


def ingest_tellmeabout_tech(
    repository: KnowledgeRepository,
    settings: Settings,
    *,
    input_path: Path | None = None,
    feed_url: str = DEFAULT_FEED_URL,
) -> dict[str, Any]:
    try:
        feed_payload = read_feed_payload(input_path=input_path, feed_url=feed_url)
    except LiveFetchUnavailable as error:
        return error.to_payload(feed_url)

    try:
        parsed = parse_feed(feed_payload.payload)
    except FeedParseError as error:
        return error.to_payload(feed_payload.ref)
    bootstrap_schema(repository.client, embedding_dimension=settings.embedding_dimension)
    now = utc_now()
    counts = empty_counts()

    source = _source_document(now, feed_url)
    counts["sources"] += int(repository.upsert("sources", source)["created"])

    raw = _raw_snapshot(feed_payload, now)
    counts["raw_snapshots"] += int(repository.upsert("raw_snapshots", raw)["created"])

    import_run_key = stable_key(SOURCE_KEY, feed_payload.kind, feed_payload.sha256[:16], now[:10], prefix="import")
    import_run: dict[str, Any] = {
        "_key": import_run_key,
        "started_at": now,
        "finished_at": None,
        "status": "running",
        "command": _command(feed_payload, feed_url),
        "source_key": SOURCE_KEY,
        "input_ref": feed_payload.ref,
        "counts": {},
        "error": None,
        "metadata": {"input": _input_payload(feed_payload)},
    }
    repository.upsert("import_runs", import_run)

    failure: Exception | None = None
    try:
        for item in parsed.items:
            counts = _ingest_item(repository, settings, item, raw, import_run_key, now, counts)
        import_run["metadata"]["skipped"] = parsed.skipped
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
        "input": _input_payload(feed_payload),
        "created": counts,
        "deduplicated": {
            "documents": max(len(parsed.items) - counts["documents"], 0),
            "chunks": max(planned_chunk_count(parsed.items) - counts["chunks"], 0),
        },
        "skipped": parsed.skipped,
    }


def read_feed_payload(*, input_path: Path | None, feed_url: str) -> FeedPayload:
    if input_path is not None:
        payload = input_path.read_text(encoding="utf-8")
        return FeedPayload(
            kind="file",
            ref=str(input_path),
            payload=payload,
            sha256=sha256_text(payload),
            media_type=detect_media_type(payload),
            storage_kind="local_file",
        )

    payload = fetch_feed_payload(feed_url)
    return FeedPayload(
        kind="url",
        ref=feed_url,
        payload=payload,
        sha256=sha256_text(payload),
        media_type=detect_media_type(payload),
        storage_kind="inline",
    )


def fetch_feed_payload(feed_url: str, *, timeout_seconds: float = 15.0) -> str:
    headers = {
        "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8",
        "User-Agent": "knowledge-base-ingest/0.1 (+https://tellmeabout.tech/)",
    }
    try:
        with open_public_url(feed_url, headers=headers, timeout=timeout_seconds) as response:
            status = getattr(response, "status", 200)
            if status >= 400:
                raise LiveFetchUnavailable(feed_url, f"HTTP {status}")
            return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
    except UnsafeUrlError as error:
        raise LiveFetchUnavailable(feed_url, f"blocked URL: {error}") from error
    except urllib.error.HTTPError as error:
        raise LiveFetchUnavailable(feed_url, f"HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise LiveFetchUnavailable(feed_url, str(error)) from error


def parse_feed(payload: str) -> ParsedSourceFeed:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as error:
        raise FeedParseError(f"malformed XML: {error}") from error
    root_name = _local_name(root.tag)
    media_type = detect_media_type(payload)

    if root_name == "rss":
        channel = root.find("channel")
        if channel is None:
            raise FeedParseError("RSS feed is missing a <channel> element")
        return _parse_rss(channel, media_type)
    if root_name == "feed":
        return _parse_atom(root, media_type)
    raise FeedParseError(f"unsupported feed root element: <{root_name}>")


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html or "")
    parser.close()
    return parser.text()


def canonical_id_from_url_or_guid(url: str | None, guid: str | None) -> str:
    # Append a short hash of the exact source string so different URLs/guids that slugify to
    # the same readable slug (e.g. /foo/bar vs /foo-bar) get distinct canonical ids and do not
    # overwrite each other's document (finding #5).
    if url:
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.strip("/")
        if path:
            return f"{slugify(path.replace('/', '-'), fallback='post')}-{sha256_text(path)[:8]}"
    fallback = guid or "post"
    return f"{slugify(fallback, fallback='post')}-{sha256_text(fallback)[:8]}"


def detect_media_type(payload: str) -> str:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return "application/xml"
    if _local_name(root.tag) == "feed":
        return "application/atom+xml"
    return "application/rss+xml"


def _parse_rss(channel: ElementTree.Element, media_type: str) -> ParsedSourceFeed:
    title = _child_text(channel, "title")
    feed_url = _child_text(channel, "link")
    items: list[NormalizedSourceItem] = []
    skipped: list[dict[str, str]] = []
    for item in channel.findall("item"):
        normalized = _rss_item(item)
        if normalized.text:
            items.append(normalized)
        else:
            skipped.append({"guid": normalized.guid or normalized.url or normalized.title, "reason": "empty_text"})
    return ParsedSourceFeed(title=title, feed_url=feed_url, media_type=media_type, items=items, skipped=skipped)


def _parse_atom(feed: ElementTree.Element, media_type: str) -> ParsedSourceFeed:
    title = _child_text(feed, "title")
    feed_url = _atom_link(feed)
    items: list[NormalizedSourceItem] = []
    skipped: list[dict[str, str]] = []
    for entry in _children(feed, "entry"):
        normalized = _atom_entry(entry)
        if normalized.text:
            items.append(normalized)
        else:
            skipped.append({"guid": normalized.guid or normalized.url or normalized.title, "reason": "empty_text"})
    return ParsedSourceFeed(title=title, feed_url=feed_url, media_type=media_type, items=items, skipped=skipped)


def _rss_item(item: ElementTree.Element) -> NormalizedSourceItem:
    title = _child_text(item, "title") or "Untitled"
    url = _child_text(item, "link")
    guid = _child_text(item, "guid")
    content = _child_text(item, "encoded") or _child_text(item, "description") or ""
    tags = [_clean_text(child.text or "") for child in item if _local_name(child.tag) == "category"]
    tags = [tag for tag in tags if tag]
    author = _child_text(item, "creator") or _child_text(item, "author")
    published_at = parse_date(_child_text(item, "pubDate"))
    canonical_id = canonical_id_from_url_or_guid(url, guid)
    return NormalizedSourceItem(
        canonical_id=canonical_id,
        title=title,
        text=html_to_text(content),
        url=url,
        guid=guid,
        published_at=published_at,
        language="unknown",
        author=author,
        tags=tags,
        metadata={"guid": guid, "feed_item_type": "rss"},
    )


def _atom_entry(entry: ElementTree.Element) -> NormalizedSourceItem:
    title = _child_text(entry, "title") or "Untitled"
    url = _atom_link(entry)
    guid = _child_text(entry, "id")
    content = _child_text(entry, "content") or _child_text(entry, "summary") or ""
    tags = []
    for child in _children(entry, "category"):
        label = child.attrib.get("label") or child.attrib.get("term") or ""
        if label:
            tags.append(_clean_text(label))
    author_node = next(iter(_children(entry, "author")), None)
    author = _child_text(author_node, "name") if author_node is not None else None
    published_at = parse_date(_child_text(entry, "published") or _child_text(entry, "updated"))
    canonical_id = canonical_id_from_url_or_guid(url, guid)
    return NormalizedSourceItem(
        canonical_id=canonical_id,
        title=title,
        text=html_to_text(content),
        url=url,
        guid=guid,
        published_at=published_at,
        language="unknown",
        author=author,
        tags=[tag for tag in tags if tag],
        metadata={"guid": guid, "feed_item_type": "atom"},
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
    provenance = _provenance(item, raw)
    metadata = {**item.metadata, "tags": item.tags, "author": item.author, "raw_snapshot_key": raw["_key"]}
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
        method="feed_category",
        evidence=lambda tag: tag,
        provenance=provenance,
    )
    upsert_author(repository, item, doc_key, SOURCE_KEY, import_run_key, now, counts, method="feed_author", provenance=provenance)
    upsert_chunks(
        repository,
        settings,
        item,
        doc_key,
        raw,
        import_run_key,
        now,
        counts,
        chunk_metadata={"source_key": SOURCE_KEY, "tags": item.tags},
        topic_method="feed_category",
        topic_evidence=lambda tag: tag,
        provenance=provenance,
    )
    return counts


def _source_document(now: str, feed_url: str) -> dict[str, Any]:
    return {
        "_key": SOURCE_KEY,
        "type": "medium_blog",
        "display_name": DISPLAY_NAME,
        "created_at": now,
        "metadata": {"site_url": SITE_URL, "feed_url": feed_url, "platform": "medium_custom_domain"},
    }


def _raw_snapshot(feed_payload: FeedPayload, now: str) -> dict[str, Any]:
    return {
        "_key": stable_key(SOURCE_KEY, feed_payload.sha256, prefix="raw"),
        "source_key": SOURCE_KEY,
        "sha256": feed_payload.sha256,
        "size_bytes": len(feed_payload.payload.encode("utf-8")),
        "media_type": feed_payload.media_type,
        "storage_kind": feed_payload.storage_kind,
        "storage_uri": feed_payload.ref,
        "captured_at": now,
        "payload": feed_payload.payload,
        "metadata": {"input_kind": feed_payload.kind, "safe_fixture": feed_payload.ref.startswith("tests/fixtures/")},
    }


def _provenance(item: NormalizedSourceItem, raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": item.url,
        "guid": item.guid,
        "raw_snapshot_key": raw["_key"],
        "source_key": SOURCE_KEY,
        "feed_item": {"canonical_id": item.canonical_id, "title": item.title, "published_at": item.published_at},
    }


def _input_payload(feed_payload: FeedPayload) -> dict[str, str]:
    return {"kind": feed_payload.kind, "ref": feed_payload.ref, "sha256": feed_payload.sha256}


def _command(feed_payload: FeedPayload, feed_url: str) -> str:
    if feed_payload.kind == "file":
        return f"kb ingest tellmeabout-tech --input {feed_payload.ref}"
    return f"kb ingest tellmeabout-tech --feed-url {feed_url}"


def _child_text(node: ElementTree.Element | None, local_name: str) -> str | None:
    if node is None:
        return None
    for child in node:
        if _local_name(child.tag) == local_name:
            return _clean_text("".join(child.itertext()))
    return None


def _children(node: ElementTree.Element, local_name: str) -> list[ElementTree.Element]:
    return [child for child in node if _local_name(child.tag) == local_name]


def _atom_link(node: ElementTree.Element) -> str | None:
    links = _children(node, "link")
    for link in links:
        if link.attrib.get("rel") in {None, "", "alternate"} and link.attrib.get("href"):
            return link.attrib["href"]
    return links[0].attrib.get("href") if links else None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").split())
