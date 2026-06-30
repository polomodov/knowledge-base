# Data Model: Book Cube Telegram Source

## Source

`sources/book-cube`

- `type`: `telegram_channel`
- `display_name`: `Книжный куб`
- `metadata.channel_url`: `https://t.me/book_cube`
- `metadata.public_preview_url`: `https://t.me/s/book_cube`
- `metadata.platform`: `telegram_public_channel`

## Raw Snapshot

One `raw_snapshots` record per HTML/JSON payload.

- `_key`: deterministic from `book-cube` and payload hash.
- `source_key`: `book-cube`
- `sha256`: payload sha256.
- `media_type`: `text/html` or `application/json`.
- `storage_kind`: `inline` for URL/synthetic small snapshots, `local_file` for local real snapshots.
- `storage_uri`: URL or file path.
- `captured_at`: import timestamp.
- `payload`: payload when inline/local parsed.
- `metadata.input_kind`: `url` or `file`.

## Documents

One document per valid text message.

- `canonical_id`: normalized Telegram post id, e.g. `book_cube-123`.
- `title`: first text line clipped to a stable short title.
- `text`: normalized message text.
- `language`: `unknown`.
- `published_at`: Telegram message timestamp.
- `url`: `https://t.me/book_cube/{message_id}` when known.
- `status`: `published`.
- `metadata`: message id, data-post, hashtags and raw snapshot key.

## Topics

Hashtags become topics.

- `_key`: slugified hashtag without `#`.
- `label`: hashtag label without `#`.
- `metadata.source`: `telegram_hashtag`.

## Edges

- `document_from_source`: document to `book-cube`.
- `chunk_of_document`: chunks to document.
- `chunk_derived_from_raw`: chunks to snapshot.
- `document_mentions_topic`: documents/chunks to hashtags.

No author or work extraction in this slice.
