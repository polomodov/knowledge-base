# Data Model: Book Cube Owner Archive Import

## Archive

Input is either:

- directory containing `result.json`; or
- `.zip` containing `result.json` at root or in a nested export directory.

Archive metadata:

- `kind`: `directory` or `zip`
- `ref`: input path
- `result_json`: path or zip member name
- `result_sha256`: sha256 of `result.json`
- `manifest_sha256`: sha256 of normalized file manifest

## Raw Snapshot

One `raw_snapshots` record per archive import payload.

- `source_key`: `book-cube`
- `sha256`: `result_sha256`
- `media_type`: `application/json`
- `storage_kind`: `local_file`
- `storage_uri`: archive path
- `payload`: `result.json` content
- `metadata.archive`: archive metadata

## Documents

One document per valid Telegram message with text or caption.

- `canonical_id`: `book_cube-{message.id}`
- `url`: `https://t.me/book_cube/{message.id}`
- `metadata.attachments[]`: local references to archive media/file paths
- `metadata.archive`: archive metadata

## Attachments

Attachment metadata is stored on documents only:

- `field`: Telegram JSON field name, e.g. `photo`, `file`, `thumbnail`
- `path`: relative path from export JSON when present
- `local_path`: filesystem path for directory archives when resolvable
- `media_type`: guessed from Telegram fields or file extension
- `mime_type`: Telegram `mime_type` when present
- `size_bytes`: local file size when available

Binary payloads are never written to ArangoDB or git.
