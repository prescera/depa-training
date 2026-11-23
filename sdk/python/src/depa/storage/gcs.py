"""Google Cloud Storage connector over the JSON API with an OAuth bearer token."""

from __future__ import annotations

import re
from contextlib import AbstractContextManager
from typing import IO
from urllib.parse import quote, urlsplit

import httpx

from depa.errors import StorageError
from depa.storage._common import (
    DEFAULT_CHUNK,
    bounded_bytes,
    check_key,
    http_client,
    raise_for_status,
    read_chunks,
    send,
    stream_get,
)

GCS_HOST = "storage.googleapis.com"
CHUNK_GRANULARITY = 256 << 10
_RANGE_RE = re.compile(r"^bytes=0-(\d+)$")


class GcsConnector:
    connector = "gcs"

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str,
        token: str,
        chunk_size: int = DEFAULT_CHUNK,
        client: httpx.Client | None = None,
    ) -> None:
        if not token:
            raise StorageError("GCS access requires a bearer token", code="no_credentials")
        if chunk_size <= 0 or chunk_size % CHUNK_GRANULARITY:
            raise StorageError("GCS chunk size must be a positive multiple of 256 KiB", code="bad_config")
        self._bucket = bucket
        self._prefix = prefix
        self.__token = token
        self._chunk_size = chunk_size
        self._client = client or http_client()

    def __repr__(self) -> str:
        return f"GcsConnector(bucket={self._bucket!r}, prefix={self._prefix!r})"

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.__token}"}

    def _name(self, key: str) -> str:
        return self._prefix + check_key(key)

    def open_read(self, key: str) -> AbstractContextManager[IO[bytes]]:
        url = f"https://{GCS_HOST}/storage/v1/b/{self._bucket}/o/{quote(self._name(key), safe='')}?alt=media"
        return stream_get(self._client, url, what=f"GCS GET {key}", headers=self._auth())

    def read_bytes(self, key: str, max_bytes: int) -> bytes:
        with self.open_read(key) as reader:
            return bounded_bytes(reader, max_bytes, f"object {key}")

    def upload(self, key: str, src: IO[bytes], size: int) -> None:
        what = f"GCS upload {key}"
        upload_url = f"https://{GCS_HOST}/upload/storage/v1/b/{self._bucket}/o"
        if size <= self._chunk_size:
            body = b"".join(read_chunks(src, size, self._chunk_size))
            response = send(
                self._client,
                "POST",
                upload_url,
                what=what,
                params={"uploadType": "media", "name": self._name(key)},
                headers={**self._auth(), "Content-Type": "application/octet-stream"},
                content=body,
            )
            raise_for_status(response, what)
            return
        response = send(
            self._client,
            "POST",
            upload_url,
            what=what,
            params={"uploadType": "resumable", "name": self._name(key)},
            headers={
                **self._auth(),
                "X-Upload-Content-Type": "application/octet-stream",
                "X-Upload-Content-Length": str(size),
                "Content-Type": "application/json; charset=UTF-8",
            },
            content=b"{}",
        )
        raise_for_status(response, what)
        session_uri = response.headers.get("location", "")
        parts = urlsplit(session_uri)
        if parts.scheme != "https" or parts.hostname != GCS_HOST:
            raise StorageError(f"{what}: unexpected resumable session URI", code="bad_response")
        offset = 0
        for chunk in read_chunks(src, size, self._chunk_size):
            end = offset + len(chunk) - 1
            # The session URI authorises the upload; the bearer token is not re-sent. Only
            # connection failures are retried: a sent chunk is never blindly replayed.
            response = send(
                self._client,
                "PUT",
                session_uri,
                what=f"{what} chunk",
                headers={"Content-Range": f"bytes {offset}-{end}/{size}"},
                content=chunk,
                retry=False,
            )
            last = end == size - 1
            if last:
                raise_for_status(response, what)
            else:
                response.close()
                if response.status_code != 308:
                    raise StorageError(f"{what}: HTTP {response.status_code} during resumable upload")
                match = _RANGE_RE.fullmatch(response.headers.get("range", ""))
                if match is None or int(match.group(1)) != end:
                    raise StorageError(f"{what}: server persisted an unexpected range", code="bad_response")
            offset = end + 1

    def uri_for(self, key: str) -> str:
        return f"gs://{self._bucket}/{self._name(key)}"
