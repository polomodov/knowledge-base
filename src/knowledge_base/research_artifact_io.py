"""Filesystem, digests, and path-security helpers for research artifacts.

Keeps secure I/O, atomic publish, and generated-zone path rules separate from
artifact orchestration and contract projection in ``research_artifacts``.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal


class ArtifactContractError(ValueError):
    """A serialized artifact violates the supported wire contract."""


class ArtifactCollisionError(FileExistsError):
    """An immutable artifact already exists with different bytes."""


class UnsafeArtifactPathError(ValueError):
    """An artifact path crosses a symlink or another unsafe filesystem boundary."""


class OutputRootAcknowledgementRequired(PermissionError):
    """A write outside data/generated requires an explicit caller acknowledgement."""


_OUTSIDE_GENERATED_WARNING = "output_outside_generated_zone"

_MANIFEST_FILENAME = "manifest.json"

_DOSSIER_FILENAME = "dossier.md"

_WRITING_OUTPUT_FILENAME = "output.md"

_VALIDATION_FILENAME = "validation.json"

_DOSSIER_PACKAGE_FILENAMES = frozenset({_MANIFEST_FILENAME, _DOSSIER_FILENAME, _VALIDATION_FILENAME})

_IMPORTED_WRITING_FILENAMES = frozenset({_MANIFEST_FILENAME, _WRITING_OUTPUT_FILENAME, _VALIDATION_FILENAME})

_MAX_DOSSIER_MEMBER_BYTES = 32 * 1024 * 1024

PublishStatus = Literal["created", "reused"]


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


def _file_digest(path: str, payload: bytes) -> dict[str, Any]:
    return {"path": path, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}


def _json_file_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _read_imported_writing_directory(package_dir: Path) -> dict[str, bytes]:
    package_path = _absolute_path(package_dir)
    assert_no_symlink_components(package_path)
    try:
        mode = os.lstat(package_path).st_mode
    except FileNotFoundError as error:
        raise ArtifactContractError(f"imported-writing directory does not exist: {package_path}") from error
    if stat.S_ISLNK(mode):
        raise UnsafeArtifactPathError(f"symlink imported-writing directory is forbidden: {package_path}")
    if not stat.S_ISDIR(mode):
        raise ArtifactContractError(f"imported-writing path is not a real directory: {package_path}")

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(package_path, flags)
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.EMLINK}:
            raise UnsafeArtifactPathError(f"symlink imported-writing directory is forbidden: {package_path}") from error
        raise ArtifactContractError(f"cannot open imported-writing directory: {package_path}") from error
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ArtifactContractError(f"imported-writing path is not a real directory: {package_path}")
        names = set(os.listdir(descriptor))
        if names != _IMPORTED_WRITING_FILENAMES:
            raise ArtifactContractError("imported-writing directory must contain exactly three known files")
        files = {name: _read_dossier_member(descriptor, package_path, name) for name in sorted(names)}
        if set(os.listdir(descriptor)) != _IMPORTED_WRITING_FILENAMES:
            raise ArtifactContractError("imported-writing directory changed while being read")
        return files
    finally:
        os.close(descriptor)


def _read_dossier_directory(revision_dir: Path) -> dict[str, bytes]:
    revision = _absolute_path(revision_dir)
    assert_no_symlink_components(revision)
    try:
        mode = os.lstat(revision).st_mode
    except FileNotFoundError as error:
        raise ArtifactContractError(f"dossier revision directory does not exist: {revision}") from error
    if stat.S_ISLNK(mode):
        raise UnsafeArtifactPathError(f"symlink dossier revision is forbidden: {revision}")
    if not stat.S_ISDIR(mode):
        raise ArtifactContractError(f"dossier revision path is not a real directory: {revision}")

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(revision, flags)
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.EMLINK}:
            raise UnsafeArtifactPathError(f"symlink dossier revision is forbidden: {revision}") from error
        raise ArtifactContractError(f"cannot open dossier revision directory: {revision}") from error
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ArtifactContractError(f"dossier revision path is not a real directory: {revision}")
        names = set(os.listdir(descriptor))
        if names != _DOSSIER_PACKAGE_FILENAMES:
            raise ArtifactContractError("dossier revision directory must contain exactly three known files")
        files = {name: _read_dossier_member(descriptor, revision, name) for name in sorted(names)}
        if set(os.listdir(descriptor)) != _DOSSIER_PACKAGE_FILENAMES:
            raise ArtifactContractError("dossier revision directory changed while being read")
        return files
    finally:
        os.close(descriptor)


def _read_dossier_member(directory_descriptor: int, revision: Path, name: str) -> bytes:
    try:
        path_stat = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError as error:
        raise ArtifactContractError(f"dossier package member is missing: {name}") from error
    if stat.S_ISLNK(path_stat.st_mode):
        raise UnsafeArtifactPathError(f"symlink dossier package member is forbidden: {revision / name}")
    if not stat.S_ISREG(path_stat.st_mode):
        raise ArtifactContractError(f"dossier package member is not a regular file: {name}")
    _validate_dossier_member_size(name, path_stat.st_size)

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.EMLINK}:
            raise UnsafeArtifactPathError(f"symlink dossier package member is forbidden: {revision / name}") from error
        raise ArtifactContractError(f"cannot open dossier package member: {name}") from error
    try:
        opened_stat = os.fstat(descriptor)
        if stat.S_ISLNK(opened_stat.st_mode):
            raise UnsafeArtifactPathError(f"symlink dossier package member is forbidden: {revision / name}")
        if not stat.S_ISREG(opened_stat.st_mode):
            raise ArtifactContractError(f"dossier package member is not a regular file: {name}")
        if (opened_stat.st_dev, opened_stat.st_ino) != (path_stat.st_dev, path_stat.st_ino):
            raise UnsafeArtifactPathError(f"dossier package member changed during secure open: {revision / name}")
        _validate_dossier_member_size(name, opened_stat.st_size)
        payload = _read_bounded_descriptor(descriptor, name)
        final_stat = os.fstat(descriptor)
        if final_stat.st_size != len(payload) or final_stat.st_mtime_ns != opened_stat.st_mtime_ns:
            raise ArtifactContractError(f"dossier package member changed while being read: {name}")
        return payload
    finally:
        os.close(descriptor)


def _validate_dossier_member_size(name: str, size: int) -> None:
    if size > _MAX_DOSSIER_MEMBER_BYTES:
        raise ArtifactContractError(f"dossier package member {name} exceeds {_MAX_DOSSIER_MEMBER_BYTES} byte limit")


def _read_bounded_descriptor(descriptor: int, name: str) -> bytes:
    payload = bytearray()
    while True:
        remaining = _MAX_DOSSIER_MEMBER_BYTES + 1 - len(payload)
        if remaining <= 0:
            raise ArtifactContractError(f"dossier package member {name} exceeds {_MAX_DOSSIER_MEMBER_BYTES} byte limit")
        chunk = os.read(descriptor, min(1024 * 1024, remaining))
        if not chunk:
            return bytes(payload)
        payload.extend(chunk)


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
