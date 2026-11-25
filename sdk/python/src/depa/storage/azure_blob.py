"""Azure Blob Storage connector over the REST API.

Authenticates either with an Entra ID bearer token or with a user-delegation SAS issued by the
TDP's federation broker (spec §10.1); exactly one of ``token`` / ``sas`` is given.
"""

from __future__ import annotations

import base64
from contextlib import AbstractContextManager
from typing import IO
from urllib.parse import parse_qsl
from xml.sax.saxutils import escape

import httpx

from depa.errors import StorageError
from depa.storage._common import (
    DEFAULT_CHUNK,
    bounded_bytes,
    check_key,
    http_client,
    quote_key,
    raise_for_status,
    read_chunks,
    send,
    stream_get,
)

API_VERSION = "2023-11-03"
MAX_BLOCKS = 50_000


class AzureBlobConnector:
    connector = "azure-blob"

    def __init__(
        self,
        *,
        account_host: str,
        container: str,
        prefix: str,
        token: str | None = None,
        sas: str | None = None,
        block_size: int = DEFAULT_CHUNK,
        client: httpx.Client | None = None,
    ) -> None:
        if bool(token) == bool(sas):
            raise StorageError("Azure Blob access requires a bearer token or a SAS", code="no_credentials")
        self._host = account_host
        self._container = container
        self._prefix = prefix
        self.__token = token
        self.__sas = sas
        self._block_size = block_size
        self._client = client or http_client()

    def __repr__(self) -> str:
        return f"AzureBlobConnector(host={self._host!r}, container={self._container!r}, prefix={self._prefix!r})"

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"x-ms-version": API_VERSION}
        if self.__token:
            headers["Authorization"] = f"Bearer {self.__token}"
        headers.update(extra or {})
        return headers

    def _url(self, key: str) -> str:
        base = f"https://{self._host}/{self._container}/{quote_key(self._prefix + check_key(key))}"
        return f"{base}?{self.__sas}" if self.__sas else base

    def _url_with_params(self, key: str, params: dict[str, str]) -> tuple[str, dict[str, str]]:
        """URL and query parameters for a request that adds its own parameters (block uploads).

        httpx replaces a URL's query string when ``params`` are given, which would drop the SAS
        signature; the SAS parameters are therefore merged into ``params`` instead.
        """
        base = f"https://{self._host}/{self._container}/{quote_key(self._prefix + check_key(key))}"
        sas = dict(parse_qsl(self.__sas, keep_blank_values=True)) if self.__sas else {}
        return base, {**sas, **params}

    def open_read(self, key: str) -> AbstractContextManager[IO[bytes]]:
        return stream_get(self._client, self._url(key), what=f"Azure GET {key}", headers=self._headers())

    def read_bytes(self, key: str, max_bytes: int) -> bytes:
        with self.open_read(key) as reader:
            return bounded_bytes(reader, max_bytes, f"object {key}")

    def upload(self, key: str, src: IO[bytes], size: int) -> None:
        url = self._url(key)
        what = f"Azure PUT {key}"
        if size <= self._block_size:
            body = b"".join(read_chunks(src, size, self._block_size))
            response = send(
                self._client,
                "PUT",
                url,
                what=what,
                headers=self._headers({"x-ms-blob-type": "BlockBlob", "Content-Type": "application/octet-stream"}),
                content=body,
            )
            raise_for_status(response, what)
            return
        if -(-size // self._block_size) > MAX_BLOCKS:
            raise StorageError("object too large for the configured block size", code="too_large")
        block_ids: list[str] = []
        for index, chunk in enumerate(read_chunks(src, size, self._block_size)):
            block_id = base64.b64encode(f"depa-{index:010d}".encode()).decode("ascii")
            block_url, block_params = self._url_with_params(key, {"comp": "block", "blockid": block_id})
            response = send(
                self._client,
                "PUT",
                block_url,
                what=f"{what} block {index}",
                headers=self._headers(),
                params=block_params,
                content=chunk,
            )
            raise_for_status(response, what)
            block_ids.append(block_id)
        body_xml = (
            '<?xml version="1.0" encoding="utf-8"?><BlockList>'
            + "".join(f"<Latest>{escape(b)}</Latest>" for b in block_ids)
            + "</BlockList>"
        ).encode()
        list_url, list_params = self._url_with_params(key, {"comp": "blocklist"})
        response = send(
            self._client,
            "PUT",
            list_url,
            what=f"{what} block list",
            headers=self._headers(
                {"Content-Type": "application/xml", "x-ms-blob-content-type": "application/octet-stream"}
            ),
            params=list_params,
            content=body_xml,
        )
        raise_for_status(response, what)

    def delete(self, key: str) -> bool:
        response = send(self._client, "DELETE", self._url(key), what=f"Azure DELETE {key}", headers=self._headers())
        if response.status_code == 404:
            return False
        raise_for_status(response, f"Azure DELETE {key}")
        return True

    def uri_for(self, key: str) -> str:
        return f"https://{self._host}/{self._container}/{quote_key(self._prefix + check_key(key))}"
