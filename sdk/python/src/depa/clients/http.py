"""HTTPS transport for the CCF services: certificate pinning, timeouts, bounded retries.

CCF / Azure Confidential Ledger service certificates are not publicly trusted, so every
service connection is pinned to the service identity certificate (PEM) obtained out of band
(``depa dev up`` writes it; on ACL it comes from the ledger identity service and is
confirmed against governance). Only calls that are safe to repeat are retried.
"""

from __future__ import annotations

import ssl
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from depa.core.encoding import loads_strict
from depa.core.keys import Signer
from depa.core.signed_request import build_signed_request
from depa.errors import ConfigError, NotFoundError, ServiceError

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_RETRIES = 3
MAX_RESPONSE_BYTES = 32 << 20
_RETRY_STATUSES = frozenset({502, 503, 504})
TXID_HEADER = "x-ms-ccf-transaction-id"


@dataclass(frozen=True)
class ServiceEndpoint:
    """A service base URL (including any path prefix such as ``/app``) and how to trust it."""

    name: str
    url: str
    ca_pem: str | None = None
    client_cert: tuple[str, str] | None = None
    bearer_token: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        parts = urlsplit(self.url)
        if parts.scheme != "https" or not parts.hostname or parts.query or parts.fragment:
            raise ConfigError(f"{self.name} URL must be an https:// base URL, got {self.url!r}")

    @property
    def base(self) -> str:
        return self.url.rstrip("/")


def pinned_ssl_context(ca_pem: str | None) -> ssl.SSLContext:
    """TLS context trusting exactly ``ca_pem`` (or the system store when ``None``)."""
    if ca_pem is None:
        return ssl.create_default_context()
    if "-----BEGIN CERTIFICATE-----" not in ca_pem:
        raise ConfigError("pinned service certificate is not a PEM certificate")
    ctx = ssl.create_default_context(cadata=ca_pem)
    # CCF node certificates do not always satisfy OpenSSL's X509_STRICT profile checks; the
    # trust decision is the pin itself, hostname verification stays enabled.
    ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def path_segment(value: str) -> str:
    """Percent-encode one path segment (``/`` always encoded; ``:`` kept, e.g. ``sha256:`` proposal ids)."""
    return quote(value, safe=":")


class ServiceHttp:
    def __init__(
        self,
        endpoint: ServiceEndpoint,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        retries: int = DEFAULT_RETRIES,
        transport: httpx.BaseTransport | None = None,
        backoff_s: float = 0.5,
    ) -> None:
        self.endpoint = endpoint
        self._retries = max(0, retries)
        self._backoff_s = backoff_s
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(timeout_s, connect=min(10.0, timeout_s)),
            "follow_redirects": False,
            "headers": {"Accept": "application/json", "User-Agent": "depa-sdk/2"},
        }
        if transport is not None:
            kwargs["transport"] = transport
        else:
            ctx = pinned_ssl_context(endpoint.ca_pem)
            if endpoint.client_cert is not None:
                ctx.load_cert_chain(*endpoint.client_cert)
            kwargs["verify"] = ctx
        self._client = httpx.Client(**kwargs)
        self.last_transaction_id: str | None = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ServiceHttp:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -----------------------------------------------------------------------------------------

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            raise ValueError("path must start with '/'")
        return self.endpoint.base + path

    def _read_body(self, response: httpx.Response) -> bytes:
        declared = response.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
            raise ServiceError("response too large", service=self.endpoint.name, status=response.status_code)
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise ServiceError("response too large", service=self.endpoint.name, status=response.status_code)
            chunks.append(chunk)
        return b"".join(chunks)

    def _raise_for_status(self, response: httpx.Response, body: bytes) -> None:
        if 200 <= response.status_code < 300:
            return
        remote_code: str | None = None
        message = f"HTTP {response.status_code}"
        try:
            parsed = loads_strict(body) if body else None
            if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
                remote_code = str(parsed["error"].get("code", "")) or None
                message = f"{message}: {str(parsed['error'].get('message', ''))[:500]}"
        except Exception:  # noqa: BLE001 - error bodies are best-effort diagnostics only
            message = f"{message}: {body[:200].decode('utf-8', 'replace')}"
        cls = NotFoundError if response.status_code == 404 else ServiceError
        raise cls(
            f"{self.endpoint.name} {response.request.method} {response.request.url.path}: {message}",
            service=self.endpoint.name,
            status=response.status_code,
            remote_code=remote_code,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        idempotent: bool | None = None,
        raw: bool = False,
    ) -> Any:
        """Send a request; returns parsed JSON (strict) or raw bytes when ``raw``.

        Retries: idempotent requests on transport errors and 502/503/504; others only when the
        connection could not be established (the request never reached the service).
        """
        is_idempotent = method in ("GET", "HEAD") if idempotent is None else idempotent
        hdrs = dict(headers or {})
        if self.endpoint.bearer_token and "Authorization" not in hdrs:
            hdrs["Authorization"] = f"Bearer {self.endpoint.bearer_token}"
        attempt = 0
        while True:
            try:
                request = self._client.build_request(
                    method, self._url(path), json=json_body, params=params, headers=hdrs
                )
                response = self._client.send(request, stream=True)
                try:
                    body = self._read_body(response)
                finally:
                    response.close()
                if response.status_code in _RETRY_STATUSES and is_idempotent and attempt < self._retries:
                    attempt += 1
                    time.sleep(self._backoff_s * 2 ** (attempt - 1))
                    continue
                self._raise_for_status(response, body)
                self.last_transaction_id = response.headers.get(TXID_HEADER)
                if raw:
                    return body
                return loads_strict(body) if body else None
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                if attempt >= self._retries:
                    raise ServiceError(
                        f"{self.endpoint.name}: cannot connect: {exc}",
                        service=self.endpoint.name,
                        code="unreachable",
                    ) from exc
            except httpx.TransportError as exc:
                if not is_idempotent or attempt >= self._retries:
                    raise ServiceError(
                        f"{self.endpoint.name}: transport error: {type(exc).__name__}",
                        service=self.endpoint.name,
                        code="transport",
                    ) from exc
            attempt += 1
            time.sleep(self._backoff_s * 2 ** (attempt - 1))

    def get(self, path: str, params: Mapping[str, str] | None = None) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, body: Any, *, headers: Mapping[str, str] | None = None) -> Any:
        return self.request("POST", path, json_body=body, headers=headers)

    def signed(self, method: str, path: str, signer: Signer, op: str, body: Mapping[str, Any]) -> Any:
        """Send a §3.2 signed request (a fresh ``jti`` per call; never retried after sending)."""
        request = build_signed_request(signer, aud=self.endpoint.name, op=op, body=body)
        return self.request(method, path, json_body=request)
