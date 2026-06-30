# Data Model: Tell Me About Tech Source

## Source

`sources/tellmeabout-tech`

- `type`: `medium_blog`
- `display_name`: `Tell Me About Tech`
- `metadata.site_url`: `https://tellmeabout.tech/`
- `metadata.feed_url`: `https://tellmeabout.tech/feed`
- `metadata.platform`: `medium_custom_domain`

## Raw Snapshot

One `raw_snapshots` record per feed payload.

Required mapping:

- `_key`: deterministic from `tellmeabout-tech`, input ref and payload hash.
- `source_key`: `tellmeabout-tech`
- `sha256`: feed XML sha256.
- `media_type`: `application/rss+xml` or `application/atom+xml`.
- `storage_kind`: `inline` for synthetic/small snapshots, `local_file` for local real snapshots.
- `storage_uri`: feed URL, file path, or snapshot ref.
- `captured_at`: import timestamp.
- `payload`: XML payload when inline.
- `metadata.input_kind`: `url` or `file`.

## Documents

One document per valid feed item/entry.

Required mapping:

- `canonical_id`: normalized canonical URL path, fallback to guid/id.
- `title`: feed title.
- `text`: plain text stripped from HTML content/description.
- `language`: `unknown` unless feed item declares language.
- `published_at`: `pubDate`, `published`, or `updated`.
- `url`: canonical post URL.
- `status`: `published`.
- `metadata`: guid/id, source feed URL, tags and original item metadata.

## Topics

Feed categories/tags become topics.

- `_key`: slugified category/tag.
- `label`: original category/tag label.
- `language`: `unknown`.
- `confidence`: `1.0`.
- `metadata.source`: `feed_category`.

## Authors

Create authors only when author metadata exists.

- `_key`: slugified author display name.
- `display_name`: feed author.
- `aliases`: empty list.
- `metadata.source`: `feed_author`.

## Edges

- `document_from_source`: document to `tellmeabout-tech`.
- `chunk_of_document`: chunks to document.
- `chunk_derived_from_raw`: chunk to feed snapshot.
- `document_mentions_topic`: document and chunks to feed categories/topics.
- `document_mentions_author`: document to feed author when present.

No `works` extraction in this slice.
