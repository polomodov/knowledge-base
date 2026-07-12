from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_SHORT_ID_PREFIX_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_SCHEMA_VERSION = "1.0"
_OUTSIDE_GENERATED_WARNING = "output_outside_generated_zone"

PublishStatus = Literal["created", "reused"]


class ArtifactContractError(ValueError):
    """A serialized artifact violates the supported wire contract."""


class ArtifactCollisionError(FileExistsError):
    """An immutable artifact already exists with different bytes."""


class ShortIdCollisionError(ArtifactCollisionError):
    """Two full digests map to the same shortened external identifier."""


class UnsafeArtifactPathError(ValueError):
    """An artifact path crosses a symlink or another unsafe filesystem boundary."""


class OutputRootAcknowledgementRequired(PermissionError):
    """A write outside data/generated requires an explicit caller acknowledgement."""


class ShortIdRegistry:
    """Create short IDs while retaining full digests for collision detection."""

    def __init__(self, *, prefix: str, length: int = 16) -> None:
        if not _SHORT_ID_PREFIX_RE.fullmatch(prefix):
            raise ValueError("short ID prefix must be lowercase and filesystem-safe")
        if not 1 <= length <= 64:
            raise ValueError("short ID length must be between 1 and 64")
        self.prefix = prefix
        self.length = length
        self._full_digest_by_id: dict[str, str] = {}

    def register(self, full_digest: str) -> str:
        if not _DIGEST_RE.fullmatch(full_digest):
            raise ArtifactContractError("full digest must be 64 lowercase hexadecimal characters")
        short_id = f"{self.prefix}-{full_digest[: self.length]}"
        registered = self._full_digest_by_id.get(short_id)
        if registered is not None and registered != full_digest:
            raise ShortIdCollisionError(f"short ID collision for {short_id}")
        self._full_digest_by_id[short_id] = full_digest
        return short_id


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize one deterministic UTF-8 JSON projection without insignificant whitespace."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def parse_strict_object(
    payload: bytes | str,
    *,
    artifact_type: str,
    required_fields: Iterable[str],
    optional_fields: Iterable[str] = (),
    max_bytes: int,
) -> dict[str, Any]:
    """Parse one bounded versioned JSON object and reject duplicate or unknown fields."""

    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    try:
        encoded = payload.encode("utf-8") if isinstance(payload, str) else payload
    except UnicodeEncodeError as error:
        raise ArtifactContractError("artifact is not valid UTF-8") from error
    if not isinstance(encoded, bytes):
        raise TypeError("payload must be bytes or str")
    if len(encoded) > max_bytes:
        raise ArtifactContractError(f"artifact exceeds {max_bytes} byte limit")

    def reject_constant(value: str) -> Any:
        raise ArtifactContractError(f"non-finite JSON number is forbidden: {value}")

    try:
        decoded = encoded.decode("utf-8")
        value = json.loads(
            decoded,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArtifactContractError("artifact is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ArtifactContractError("artifact must be a JSON object")

    required = {"schema_version", "artifact_type", *required_fields}
    allowed = required | set(optional_fields)
    unknown = sorted(set(value) - allowed)
    missing = sorted(required - set(value))
    if unknown:
        raise ArtifactContractError(f"unknown artifact fields: {', '.join(unknown)}")
    if missing:
        raise ArtifactContractError(f"missing artifact fields: {', '.join(missing)}")
    if value["schema_version"] != _SCHEMA_VERSION:
        raise ArtifactContractError(f"unsupported schema_version: {value['schema_version']!r}")
    if value["artifact_type"] != artifact_type:
        raise ArtifactContractError(f"unexpected artifact_type: {value['artifact_type']!r}")
    return value


def safe_http_url(value: Any) -> str | None:
    """Return a canonical HTTP(S) URL or null without opening or resolving it."""

    if not isinstance(value, str) or not value or len(value) > 4096 or value != value.strip():
        return None
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        return None
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if scheme not in {"http", "https"} or not hostname or parsed.username is not None or parsed.password is not None:
        return None
    if "\\" in parsed.netloc:
        return None

    canonical_host = hostname.lower()
    if ":" in canonical_host:
        canonical_host = f"[{canonical_host}]"
    netloc = canonical_host if port is None else f"{canonical_host}:{port}"
    return urlunsplit((scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def validate_output_root(
    output_root: Path,
    *,
    generated_root: Path,
    acknowledge_unsafe: bool,
) -> str | None:
    """Classify a write root and require acknowledgement outside the generated zone."""

    assert_no_symlink_components(generated_root)
    assert_no_symlink_components(output_root)
    output = _absolute_path(output_root)
    generated = _absolute_path(generated_root)
    if output.is_relative_to(generated):
        return None
    if not acknowledge_unsafe:
        raise OutputRootAcknowledgementRequired(
            f"output root {output} is outside generated zone {generated}; explicit acknowledgement is required"
        )
    return _OUTSIDE_GENERATED_WARNING


def assert_no_symlink_components(path: Path) -> None:
    """Reject a symlink in every currently existing component, including the target."""

    absolute = _absolute_path(path)
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            continue
        except NotADirectoryError as error:
            raise UnsafeArtifactPathError(f"non-directory path component: {current}") from error
        if stat.S_ISLNK(mode):
            raise UnsafeArtifactPathError(f"symlink path component is forbidden: {current}")


def publish_file_atomic(target: Path, payload: bytes) -> PublishStatus:
    """Publish one immutable owner-only file, reusing only byte-identical content."""

    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    destination = _absolute_path(target)
    assert_no_symlink_components(destination)
    _ensure_owner_directory(destination.parent)
    assert_no_symlink_components(destination)
    if os.path.lexists(destination):
        return _reuse_file_or_raise(destination, payload)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        handle = os.fdopen(descriptor, "wb")
        descriptor = -1
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            return _reuse_file_or_raise(destination, payload)
        _fsync_directory(destination.parent)
        return "created"
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        _fsync_directory(destination.parent)


def publish_directory_atomic(target: Path, files: Mapping[str, bytes]) -> PublishStatus:
    """Publish one immutable flat directory package with owner-only files."""

    validated_files = _validated_package_files(files)
    destination = _absolute_path(target)
    assert_no_symlink_components(destination)
    _ensure_owner_directory(destination.parent)
    assert_no_symlink_components(destination)
    if os.path.lexists(destination):
        return _reuse_directory_or_raise(destination, validated_files)

    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent))
    os.chmod(temporary, 0o700)
    try:
        for name, payload in sorted(validated_files.items()):
            _write_owner_file(temporary / name, payload)
        _fsync_directory(temporary)
        assert_no_symlink_components(destination)
        if os.path.lexists(destination):
            return _reuse_directory_or_raise(destination, validated_files)
        try:
            os.rename(temporary, destination)
        except OSError:
            if os.path.lexists(destination):
                return _reuse_directory_or_raise(destination, validated_files)
            raise
        _fsync_directory(destination.parent)
        return "created"
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
            _fsync_directory(destination.parent)


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ArtifactContractError(f"duplicate JSON field: {key}")
        value[key] = item
    return value


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _ensure_owner_directory(path: Path) -> None:
    absolute = _absolute_path(path)
    assert_no_symlink_components(absolute)
    missing: list[Path] = []
    current = absolute
    while not os.path.lexists(current):
        missing.append(current)
        parent = current.parent
        if parent == current:
            raise UnsafeArtifactPathError(f"cannot establish artifact directory: {absolute}")
        current = parent
    mode = os.lstat(current).st_mode
    if not stat.S_ISDIR(mode):
        raise UnsafeArtifactPathError(f"artifact parent is not a directory: {current}")

    for directory in reversed(missing):
        try:
            os.mkdir(directory, 0o700)
        except FileExistsError:
            assert_no_symlink_components(directory)
        mode = os.lstat(directory).st_mode
        if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
            raise UnsafeArtifactPathError(f"artifact path is not a real directory: {directory}")
        os.chmod(directory, 0o700)

    assert_no_symlink_components(absolute)
    mode = os.lstat(absolute).st_mode
    if not stat.S_ISDIR(mode):
        raise UnsafeArtifactPathError(f"artifact parent is not a directory: {absolute}")
    if stat.S_IMODE(mode) != 0o700:
        raise UnsafeArtifactPathError(f"artifact directory must have mode 0700: {absolute}")


def _validated_package_files(files: Mapping[str, bytes]) -> dict[str, bytes]:
    validated: dict[str, bytes] = {}
    for name, payload in files.items():
        if not isinstance(name, str) or not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
            raise UnsafeArtifactPathError(f"package file must be one safe relative name: {name!r}")
        if not isinstance(payload, bytes):
            raise TypeError(f"package payload for {name!r} must be bytes")
        validated[name] = payload
    return validated


def _write_owner_file(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _reuse_file_or_raise(path: Path, payload: bytes) -> PublishStatus:
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError as error:
        raise ArtifactCollisionError(f"artifact disappeared during collision check: {path}") from error
    if stat.S_ISLNK(mode):
        raise UnsafeArtifactPathError(f"symlink artifact target is forbidden: {path}")
    if not stat.S_ISREG(mode) or path.read_bytes() != payload:
        raise ArtifactCollisionError(f"immutable artifact collision: {path}")
    os.chmod(path, 0o600)
    return "reused"


def _reuse_directory_or_raise(path: Path, files: Mapping[str, bytes]) -> PublishStatus:
    mode = os.lstat(path).st_mode
    if stat.S_ISLNK(mode):
        raise UnsafeArtifactPathError(f"symlink artifact target is forbidden: {path}")
    if not stat.S_ISDIR(mode):
        raise ArtifactCollisionError(f"immutable artifact collision: {path}")
    entries = {entry.name: entry for entry in os.scandir(path)}
    if set(entries) != set(files):
        raise ArtifactCollisionError(f"immutable artifact collision: {path}")
    for name, payload in files.items():
        entry = entries[name]
        if entry.is_symlink() or not entry.is_file(follow_symlinks=False) or Path(entry.path).read_bytes() != payload:
            raise ArtifactCollisionError(f"immutable artifact collision: {path / name}")
    os.chmod(path, 0o700)
    for name in files:
        os.chmod(path / name, 0o600)
    return "reused"


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
