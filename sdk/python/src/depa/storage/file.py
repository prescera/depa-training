"""Local filesystem connector (``file:///abs/dir/``). Development / ``virtual`` platform only on a CCR."""

from __future__ import annotations

import os
import secrets
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

from depa.errors import StorageError
from depa.storage._common import DEFAULT_CHUNK, bounded_bytes, check_key, read_chunks

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


class FileConnector:
    connector = "file"

    def __init__(self, root: Path) -> None:
        if not root.is_absolute():
            raise StorageError("file connector root must be absolute", code="bad_location")
        self._root = root

    def _root_resolved(self, *, create: bool) -> Path:
        if create:
            self._root.mkdir(parents=True, exist_ok=True)
        try:
            return self._root.resolve(strict=True)
        except FileNotFoundError as exc:
            raise StorageError(f"location root {self._root} does not exist", code="not_found") from exc

    def _existing(self, key: str) -> Path:
        """Resolve ``key`` to an existing file, refusing any symlink on the way."""
        root = self._root_resolved(create=False)
        expected = root / check_key(key)
        try:
            resolved = expected.resolve(strict=True)
        except FileNotFoundError as exc:
            raise StorageError(f"object {key} not found", code="not_found") from exc
        if resolved != expected:
            raise StorageError(f"object {key} traverses a symlink", code="symlink")
        return resolved

    @contextmanager
    def open_read(self, key: str) -> Iterator[IO[bytes]]:
        path = self._existing(key)
        try:
            fd = os.open(path, os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC)
        except OSError as exc:
            raise StorageError(f"cannot open object {key}: {exc.strerror}") from exc
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise StorageError(f"object {key} is not a regular file")
        with os.fdopen(fd, "rb") as handle:
            yield handle

    def read_bytes(self, key: str, max_bytes: int) -> bytes:
        with self.open_read(key) as handle:
            return bounded_bytes(handle, max_bytes, f"object {key}")

    def upload(self, key: str, src: IO[bytes], size: int) -> None:
        root = self._root_resolved(create=True)
        target = root / check_key(key)
        current = root
        for part in target.relative_to(root).parts[:-1]:
            current = current / part
            if current.is_symlink():
                raise StorageError(f"object {key} traverses a symlink", code="symlink")
            current.mkdir(exist_ok=True)
            if not current.is_dir():
                raise StorageError(f"object {key}: {part} is not a directory")
        tmp = target.with_name(f".{target.name}.{secrets.token_hex(8)}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC, 0o644)
        try:
            with os.fdopen(fd, "wb") as out:
                for chunk in read_chunks(src, size, DEFAULT_CHUNK):
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            # os.replace swaps the directory entry itself; a symlink at ``target`` is replaced, not followed.
            tmp.replace(target)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def uri_for(self, key: str) -> str:
        return (self._root / check_key(key)).as_uri()
