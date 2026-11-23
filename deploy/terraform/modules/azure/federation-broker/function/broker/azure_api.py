"""Managed-identity token and user delegation key (Azure REST, stdlib only)."""

from __future__ import annotations

import json
import os
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from xml.etree import ElementTree

from .sas import SAS_VERSION, DelegationKey, format_time

_TIMEOUT_S = 10
_KEY_LIFETIME = timedelta(hours=6)
_KEY_MIN_REMAINING = timedelta(hours=1)


def managed_identity_token(client_id: str, resource: str = "https://storage.azure.com/") -> str:
    """Access token of the user-assigned identity via the App Service identity endpoint."""
    query = urllib.parse.urlencode({"resource": resource, "api-version": "2019-08-01", "client_id": client_id})
    request = urllib.request.Request(  # noqa: S310 - fixed platform endpoint from the environment
        f"{os.environ['IDENTITY_ENDPOINT']}?{query}",
        headers={"X-IDENTITY-HEADER": os.environ["IDENTITY_HEADER"]},
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:  # noqa: S310
        return str(json.load(response)["access_token"])


def fetch_delegation_key(blob_endpoint: str, token: str, start: datetime, expiry: datetime) -> DelegationKey:
    body = (
        f"<?xml version=\"1.0\" encoding=\"utf-8\"?><KeyInfo><Start>{format_time(start)}</Start>"
        f"<Expiry>{format_time(expiry)}</Expiry></KeyInfo>"
    ).encode()
    request = urllib.request.Request(  # noqa: S310 - https endpoint from configuration
        f"{blob_endpoint.rstrip('/')}/?restype=service&comp=userdelegationkey",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "x-ms-version": SAS_VERSION,
            "Content-Type": "application/xml",
        },
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:  # noqa: S310
        root = ElementTree.fromstring(response.read())  # noqa: S314 - Azure Storage response over TLS

    def text(tag: str) -> str:
        value = root.findtext(tag)
        if not value:
            raise RuntimeError(f"user delegation key response lacks {tag}")
        return value

    return DelegationKey(
        signed_oid=text("SignedOid"),
        signed_tid=text("SignedTid"),
        signed_start=text("SignedStart"),
        signed_expiry=text("SignedExpiry"),
        signed_service=text("SignedService"),
        signed_version=text("SignedVersion"),
        value=text("Value"),
    )


class DelegationKeyCache:
    """Reuses one delegation key until it gets close to expiry (keys may live up to 7 days)."""

    def __init__(self, blob_endpoint: str, client_id: str) -> None:
        self._blob_endpoint = blob_endpoint
        self._client_id = client_id
        self._key: DelegationKey | None = None
        self._lock = threading.Lock()

    def get(self, now: datetime, needed_until: datetime) -> DelegationKey:
        with self._lock:
            key = self._key
            if key is None or key.expiry() < max(needed_until, now + _KEY_MIN_REMAINING):
                token = managed_identity_token(self._client_id)
                key = fetch_delegation_key(self._blob_endpoint, token, now - timedelta(minutes=5), now + _KEY_LIFETIME)
                self._key = key
            return key
