"""User-delegation SAS for one blob container (string-to-sign of service version SAS_VERSION)."""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

SAS_VERSION = "2026-10-06"
PERMISSIONS = {"read": "rl", "write": "cw"}


@dataclass(frozen=True)
class DelegationKey:
    signed_oid: str
    signed_tid: str
    signed_start: str
    signed_expiry: str
    signed_service: str
    signed_version: str
    value: str  # base64

    def expiry(self) -> datetime:
        return parse_time(self.signed_expiry)


def format_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def container_sas(
    account: str, container: str, access: str, start: datetime, expiry: datetime, key: DelegationKey
) -> str:
    """Query string of an HTTPS-only user-delegation SAS on `container` (sr=c)."""
    permissions = PERMISSIONS[access]
    st, se = format_time(start), format_time(expiry)
    fields = [
        permissions, st, se, f"/blob/{account}/{container}",
        key.signed_oid, key.signed_tid, key.signed_start, key.signed_expiry,
        key.signed_service, key.signed_version,
        "", "", "", "", "",  # saoid, suoid, scid, skdutid, sduoid
        "", "https", SAS_VERSION, "c",  # sip, spr, sv, sr
        "", "", "", "",  # snapshot time, encryption scope, srh, srq
        "", "", "", "", "",  # rscc, rscd, rsce, rscl, rsct
    ]  # fmt: skip
    string_to_sign = "\n".join(fields)
    signature = base64.b64encode(
        hmac.new(base64.b64decode(key.value), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    ).decode("ascii")
    query = {
        "sp": permissions, "st": st, "se": se,
        "skoid": key.signed_oid, "sktid": key.signed_tid, "skt": key.signed_start,
        "ske": key.signed_expiry, "sks": key.signed_service, "skv": key.signed_version,
        "spr": "https", "sv": SAS_VERSION, "sr": "c", "sig": signature,
    }  # fmt: skip
    return "&".join(f"{k}={quote(v, safe='')}" for k, v in query.items())
