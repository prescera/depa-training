"""Read-only HTTPS connector (public or pre-authorised URLs; objects are encrypted anyway)."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import IO

import httpx

from depa.errors import StorageError
from depa.storage._common import bounded_bytes, http_client, quote_key, stream_get


class HttpsConnector:
    connector = "https"

    def __init__(self, base_url: str, *, client: httpx.Client | None = None) -> None:
        if not base_url.endswith("/"):
            raise StorageError("https location must end with '/'", code="bad_location")
        self._base = base_url
        self._client = client or http_client()

    def open_read(self, key: str) -> AbstractContextManager[IO[bytes]]:
        return stream_get(self._client, self.uri_for(key), what=f"GET {key}")

    def read_bytes(self, key: str, max_bytes: int) -> bytes:
        with self.open_read(key) as reader:
            return bounded_bytes(reader, max_bytes, f"object {key}")

    def upload(self, key: str, src: IO[bytes], size: int) -> None:
        raise StorageError("the https connector is read-only", code="read_only")

    def uri_for(self, key: str) -> str:
        return self._base + quote_key(key)
