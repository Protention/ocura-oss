# SPDX-License-Identifier: Apache-2.0

"""Lossless physical storage for logs whose logical bytes remain unchanged.

Callers serialize reads and mutations of a logical log. A completed gzip is
published before its raw counterpart is removed, so interrupted compression
always leaves at least one complete representation. Neither operation changes
the ledger's recorded paths, byte counts, or content digests.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import io
import os
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast

from ocura_oss.errors import classify_os_error

DEFAULT_MIN_BYTES = 1024 * 1024
_CHUNK = 1024 * 1024


class LogStorageError(Exception):
    """A log representation is missing, unsafe, unreadable, or inconsistent."""

    def __init__(self, message: str, *, code: str = "integrity_error") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StorageResult:
    """The resulting physical representation of one logical log."""

    action: str
    original_bytes: int
    stored_bytes: int
    path: Path


def _read_error_code(exc: BaseException) -> str:
    if isinstance(exc, OSError) and not isinstance(exc, gzip.BadGzipFile):
        return classify_os_error(exc)
    return "integrity_error"


def _paths(path: Path) -> tuple[Path, Path]:
    raw = Path(path)
    compressed = raw.with_name(f"{raw.name}.gz")
    try:
        parent = raw.parent.resolve()
        for candidate in (raw, compressed):
            # Check the alternative independently: a safe raw path says
            # nothing about a symlink occupying its gzip counterpart.
            if candidate.is_symlink() or candidate.resolve().parent != parent:
                raise LogStorageError(f"unsafe log path: {candidate}")
            if candidate.exists() and not candidate.is_file():
                raise LogStorageError(f"log path is not a regular file: {candidate}")
    except (OSError, ValueError) as exc:
        raise LogStorageError(f"log path is unreadable: {raw}", code=_read_error_code(exc)) from exc
    return raw, compressed


def physical_path(path: Path) -> Path:
    """Resolve storage without checking content, preferring a raw copy.

    If neither representation exists, return the logical raw path. This
    preserves path resolution's usefulness before a log has been written.
    """
    raw, compressed = _paths(path)
    return compressed if not raw.is_file() and compressed.is_file() else raw


def _check_size(actual: int, expected: int, path: Path) -> None:
    if actual != expected:
        raise LogStorageError(f"log size mismatch for {path}: recorded {expected}, found {actual}")


def _check_digest(actual: str, expected: str, path: Path) -> None:
    if actual != expected:
        raise LogStorageError(f"log checksum mismatch for {path}")


def _validate_size(size: int) -> None:
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise LogStorageError("recorded log size must be a nonnegative integer")


def _consume(
    source: BinaryIO | gzip.GzipFile,
    size: int,
    digest: str,
    path: Path,
    *,
    collect: bool = False,
    sink: BinaryIO | gzip.GzipFile | None = None,
) -> bytes | None:
    measured = 0
    checksum = hashlib.sha256()
    contents = io.BytesIO() if collect else None
    while True:
        # The extra byte both bounds expansion and asks gzip to validate its
        # footer and any trailing members even when output size matches.
        chunk = source.read(min(_CHUNK, size - measured + 1))
        if not chunk:
            break
        measured += len(chunk)
        if measured > size:
            _check_size(measured, size, path)
        checksum.update(chunk)
        if contents is not None:
            contents.write(chunk)
        if sink is not None:
            sink.write(chunk)
    _check_size(measured, size, path)
    _check_digest(checksum.hexdigest(), digest, path)
    return contents.getvalue() if contents is not None else None


def _verify(
    path: Path,
    size: int,
    digest: str,
    *,
    gzipped: bool,
    collect: bool = False,
    sink: BinaryIO | None = None,
) -> bytes | None:
    """Verify one representation; collected bytes are exactly the bytes hashed."""
    try:
        with gzip.open(path, "rb") if gzipped else path.open("rb") as source:
            return _consume(source, size, digest, path, collect=collect, sink=sink)
    except (OSError, EOFError, zlib.error, ValueError) as exc:
        raise LogStorageError(f"log is unreadable: {path}", code=_read_error_code(exc)) from exc


def read_verified(
    path: Path,
    size: int,
    digest: str,
    *,
    collect: bool = False,
    sink: BinaryIO | None = None,
) -> tuple[Path, bytes | None]:
    """Verify logical bytes in every present representation of a log.

    Raw data takes precedence for returned bytes, but a second gzip copy is
    verified as well so duplicate storage cannot hide corrupted evidence.
    Gzip expansion is limited to the recorded size plus one detection byte.
    """
    _validate_size(size)
    raw, compressed = _paths(path)
    if raw.is_file():
        data = _verify(raw, size, digest, collect=collect, sink=sink, gzipped=False)
        if compressed.is_file():
            _verify(compressed, size, digest, gzipped=True)
        return raw, data
    if compressed.is_file():
        return compressed, _verify(
            compressed, size, digest, collect=collect, sink=sink, gzipped=True
        )
    raise LogStorageError(f"log is missing: {raw}")


class _RangeWriter:
    """Retain only a byte window while the verifier consumes the whole stream."""

    def __init__(self, sink: BinaryIO, offset: int, length: int) -> None:
        self.sink, self.offset, self.length, self.position = sink, offset, length, 0

    def write(self, data: bytes) -> int:
        start = max(0, self.offset - self.position)
        end = min(len(data), self.offset + self.length - self.position)
        if start < end:
            self.sink.write(data[start:end])
        self.position += len(data)
        return len(data)


def verified_snapshot(
    path: Path, size: int, digest: str, *, offset: int = 0, length: int | None = None
) -> BinaryIO:
    """Return a seekable private snapshot, releasing no bytes before verification.

    Small streams stay in memory. Larger streams use OS temporary storage, with
    bounded read buffers and one decoding pass. The caller owns the returned file.
    """
    _validate_size(size)
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= size:
        raise LogStorageError(
            f"log offset must be between 0 and original size {size}", code="invalid_input"
        )
    if length is not None and (
        isinstance(length, bool) or not isinstance(length, int) or length < 0
    ):
        raise LogStorageError("log length must be a nonnegative integer", code="invalid_input")
    extent = size - offset if length is None else min(length, size - offset)
    # Ownership passes to the caller; failures close it below.
    snapshot = cast(
        BinaryIO,
        tempfile.TemporaryFile() if extent > _CHUNK else io.BytesIO(),  # noqa: SIM115
    )
    try:
        read_verified(
            path, size, digest, sink=cast(BinaryIO, _RangeWriter(snapshot, offset, extent))
        )
        snapshot.seek(0)
        return snapshot
    except BaseException:
        snapshot.close()
        raise


class _CountingWriter:
    """Count exact encoded bytes for a dry run without retaining or writing them."""

    def __init__(self) -> None:
        self.size = 0

    def write(self, data: bytes) -> int:
        self.size += len(data)
        return len(data)


def _encode(raw: Path, destination: BinaryIO, size: int, digest: str) -> None:
    with (
        gzip.GzipFile(
            filename="", mode="wb", compresslevel=6, fileobj=destination, mtime=0
        ) as encoded,
        raw.open("rb") as source,
    ):
        _consume(source, size, digest, raw, sink=encoded)


def _worth_compressing(stored: int, original: int) -> bool:
    return stored * 10 <= original * 9


def _temporary(path: Path) -> tuple[int, Path]:
    # Keep unfinished files out of logs/, whose orphan scan deliberately
    # treats unrelated entries as integrity problems.
    descriptor, name = tempfile.mkstemp(prefix=".ocura-log-", suffix=".tmp", dir=path.parent.parent)
    return descriptor, Path(name)


def _discard(path: Path | None) -> None:
    if path is not None:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)


def compress(
    path: Path,
    size: int,
    digest: str,
    *,
    min_bytes: int = DEFAULT_MIN_BYTES,
    dry_run: bool = False,
) -> StorageResult:
    """Replace eligible raw storage with a verified deterministic gzip.

    Only publish gzip when it saves at least ten percent. An existing valid
    gzip can finish an interrupted publication by removing its raw duplicate.
    Dry runs compute the exact compressed size without writing any file.
    """
    if isinstance(min_bytes, bool) or not isinstance(min_bytes, int) or min_bytes < 0:
        raise LogStorageError("minimum log size must be a nonnegative integer")
    _validate_size(size)
    temporary: Path | None = None
    try:
        raw, compressed = _paths(path)
        if not raw.is_file():
            if not compressed.is_file():
                raise LogStorageError(f"log is missing: {raw}")
            _verify(compressed, size, digest, gzipped=True)
            return StorageResult("unchanged", size, compressed.stat().st_size, compressed)
        if compressed.is_file():
            _verify(raw, size, digest, gzipped=False)
            _verify(compressed, size, digest, gzipped=True)
            stored = compressed.stat().st_size
            if not _worth_compressing(stored, size):
                return StorageResult("no-savings", size, size, raw)
            if not dry_run:
                raw.unlink()
            return StorageResult(
                "would-compress" if dry_run else "compressed", size, stored, compressed
            )
        if size < min_bytes:
            _verify(raw, size, digest, gzipped=False)
            return StorageResult("small", size, size, raw)
        if dry_run:
            counter = _CountingWriter()
            _encode(raw, cast(BinaryIO, counter), size, digest)
            stored = counter.size
            if not _worth_compressing(stored, size):
                return StorageResult("no-savings", size, size, raw)
            return StorageResult("would-compress", size, stored, compressed)
        descriptor, temporary = _temporary(raw)
        with os.fdopen(descriptor, "wb") as destination:
            _encode(raw, destination, size, digest)
            destination.flush()
            os.fsync(destination.fileno())
        stored = temporary.stat().st_size
        if not _worth_compressing(stored, size):
            return StorageResult("no-savings", size, size, raw)
        _verify(temporary, size, digest, gzipped=True)
        os.replace(temporary, compressed)
        temporary = None
        # Renaming preserves the verified file's bytes; the caller's lock
        # excludes another storage operation until the raw copy is removed.
        raw.unlink()
        return StorageResult("compressed", size, stored, compressed)
    except (OSError, EOFError, zlib.error, ValueError) as exc:
        raise LogStorageError(f"cannot compress log: {path}") from exc
    finally:
        _discard(temporary)


def restore(path: Path, size: int, digest: str, *, dry_run: bool = False) -> StorageResult:
    """Restore verified raw bytes, publishing them before removing gzip storage."""
    _validate_size(size)
    temporary: Path | None = None
    try:
        raw, compressed = _paths(path)
        if not compressed.is_file():
            if not raw.is_file():
                raise LogStorageError(f"log is missing: {raw}")
            _verify(raw, size, digest, gzipped=False)
            return StorageResult("unchanged", size, size, raw)
        if raw.is_file():
            _verify(raw, size, digest, gzipped=False)
            _verify(compressed, size, digest, gzipped=True)
        elif dry_run:
            _verify(compressed, size, digest, gzipped=True)
        else:
            descriptor, temporary = _temporary(raw)
            with os.fdopen(descriptor, "wb") as destination:
                _verify(compressed, size, digest, sink=destination, gzipped=True)
                destination.flush()
                os.fsync(destination.fileno())
            _verify(temporary, size, digest, gzipped=False)
            os.replace(temporary, raw)
            temporary = None
        if dry_run:
            return StorageResult("would-restore", size, size, raw)
        compressed.unlink()
        return StorageResult("restored", size, size, raw)
    except (OSError, EOFError, zlib.error, ValueError) as exc:
        raise LogStorageError(f"cannot restore log: {path}") from exc
    finally:
        _discard(temporary)
