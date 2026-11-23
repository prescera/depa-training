"""Helpers shared by the HTTP-based connectors: key validation, bounded retries, streaming."""

from __future__ import annotations

import io
import ipaddress
import re
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import IO
from urllib.parse import quote

import httpx

from depa.errors import StorageError, ValidationError

TRANSIENT_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4  # one attempt plus three retries
BACKOFF_S = 0.5
DEFAULT_CHUNK = 8 << 20
STREAM_CHUNK = 1 << 16
_SEGMENT_RE = re.compile(r"^[^\x00-\x1f\x7f\\%?#]{1,255}$")


def check_key(key: object) -> str:
    """An object key relative to the location prefix: no absolute paths, ``..``, empty segments."""
    if not isinstance(key, str) or not key or len(key) > 1024:
        raise ValidationError(f"invalid object key {key!r}", code="bad_key")
    for part in key.split("/"):
        if part in ("", ".", "..") or not _SEGMENT_RE.fullmatch(part):
            raise ValidationError(f"invalid object key {key!r}", code="bad_key")
    return key


def quote_key(key: str) -> str:
    """Percent-encode each path segment of a (validated) key."""
    return "/".join(quote(part, safe="") for part in check_key(key).split("/"))


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def http_client(timeout_s: float = 60.0) -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(timeout_s, connect=10.0),
        follow_redirects=False,
        headers={"User-Agent": "depa-sdk/2", "Accept-Encoding": "identity"},
    )


def send(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    what: str,
    headers: Mapping[str, str] | None = None,
    content: bytes | None = None,
    data: Mapping[str, str] | None = None,
    params: Mapping[str, str] | None = None,
    stream: bool = False,
    retry: bool = True,
) -> httpx.Response:
    """Send with bounded retries on transport errors and transient statuses.

    Retries only happen before a response body is consumed, so callers that stream a
    successful response are never replayed mid-stream. Error messages never carry headers.
    """
    attempt = 0
    while True:
        try:
            request = client.build_request(
                method, url, headers=dict(headers or {}), content=content, data=data, params=params
            )
            response = client.send(request, stream=stream)
        except httpx.TransportError as exc:
            if not retry or attempt >= MAX_ATTEMPTS - 1:
                raise StorageError(f"{what}: transport error ({type(exc).__name__})", code="transport") from exc
        else:
            if retry and response.status_code in TRANSIENT_STATUSES and attempt < MAX_ATTEMPTS - 1:
                response.close()
            else:
                return response
        time.sleep(BACKOFF_S * (2**attempt))
        attempt += 1


def raise_for_status(response: httpx.Response, what: str) -> None:
    if 200 <= response.status_code < 300:
        return
    response.close()
    if response.status_code == 404:
        raise StorageError(f"{what}: not found", code="not_found")
    raise StorageError(f"{what}: HTTP {response.status_code}")


def read_capped(response: httpx.Response, max_bytes: int, what: str) -> bytes:
    """Read a (streamed or buffered) response body, failing if it exceeds ``max_bytes``."""
    chunks: list[bytes] = []
    size = 0
    try:
        for chunk in response.iter_bytes(STREAM_CHUNK):
            size += len(chunk)
            if size > max_bytes:
                raise StorageError(f"{what}: object exceeds {max_bytes} bytes", code="too_large")
            chunks.append(chunk)
    finally:
        response.close()
    return b"".join(chunks)


class ResponseReader(io.RawIOBase):
    """Binary file-like view over a streamed ``httpx.Response`` body."""

    def __init__(self, response: httpx.Response) -> None:
        super().__init__()
        self._response = response
        self._iter = response.iter_bytes(STREAM_CHUNK)
        self._buffer = b""

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: memoryview | bytearray) -> int:  # type: ignore[override]
        if not self._buffer:
            try:
                self._buffer = next(self._iter)
            except StopIteration:
                return 0
            except httpx.HTTPError as exc:
                raise StorageError(f"download interrupted ({type(exc).__name__})", code="transport") from exc
        n = min(len(buffer), len(self._buffer))
        buffer[:n] = self._buffer[:n]
        self._buffer = self._buffer[n:]
        return n

    def close(self) -> None:
        if not self.closed:
            self._response.close()
        super().close()


@contextmanager
def stream_get(
    client: httpx.Client, url: str, *, what: str, headers: Mapping[str, str] | None = None
) -> Iterator[IO[bytes]]:
    response = send(client, "GET", url, what=what, headers=headers, stream=True)
    raise_for_status(response, what)
    reader = ResponseReader(response)
    try:
        yield reader  # type: ignore[misc]
    finally:
        reader.close()


def read_exact(src: IO[bytes], size: int) -> bytes:
    """Read exactly ``size`` bytes (fewer only at end of stream)."""
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = src.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_chunks(src: IO[bytes], size: int, chunk_size: int) -> Iterator[bytes]:
    """Yield ``size`` bytes from ``src`` in ``chunk_size`` pieces; fails on a short source."""
    remaining = size
    while remaining > 0:
        chunk = read_exact(src, min(chunk_size, remaining))
        if not chunk:
            raise StorageError("upload source ended before the declared size", code="short_source")
        remaining -= len(chunk)
        yield chunk
    if src.read(1):
        raise StorageError("upload source is longer than the declared size", code="long_source")


def bounded_bytes(reader: IO[bytes], max_bytes: int, what: str) -> bytes:
    data = read_exact(reader, max_bytes + 1)
    if len(data) > max_bytes:
        raise StorageError(f"{what}: object exceeds {max_bytes} bytes", code="too_large")
    return data
