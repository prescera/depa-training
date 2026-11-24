"""Storage connectors (spec §7.2 ``location``) and credential federation (spec §10).

A ``Connector`` addresses objects by key relative to the location prefix (for datasets:
``manifest.json``, ``manifest.jws``, ``objects/<path>.dedf``). Inside a CCR, credentials come
only from the KMS-issued OIDC token exchanged with the cloud STS (``depa.storage.federation``);
outside a CCR, parties use their own ambient credentials.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import IO, Protocol

CONNECTORS = ("file", "https", "s3", "azure-blob", "gcs")

# Audience-for-token callback: given the audience the cloud STS expects, return the KMS OIDC JWT.
TokenSource = Callable[[str], str]


class Connector(Protocol):
    connector: str

    def open_read(self, key: str) -> AbstractContextManager[IO[bytes]]:
        """Stream an object. Raises ``StorageError`` (code ``not_found`` when absent)."""
        ...

    def read_bytes(self, key: str, max_bytes: int) -> bytes:
        """Read a small object fully; fails if it exceeds ``max_bytes``."""
        ...

    def upload(self, key: str, src: IO[bytes], size: int) -> None:
        """Stream ``size`` bytes from ``src`` to the object ``key`` (multipart/blocks when large)."""
        ...

    def delete(self, key: str) -> bool:
        """Delete one object; ``False`` when it did not exist (idempotent)."""
        ...

    def uri_for(self, key: str) -> str:
        """Absolute URI of ``key`` (recorded in receipts)."""
        ...
