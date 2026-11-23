"""RFC 3339 UTC timestamps and NumericDate helpers (spec §2)."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime

from depa.errors import ValidationError

_RFC3339_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$")


def now_s() -> int:
    return int(time.time())


def utcnow() -> datetime:
    return datetime.now(UTC)


def format_rfc3339(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValidationError("naive datetime", code="bad_time")
    return moment.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_rfc3339() -> str:
    return format_rfc3339(utcnow())


def parse_rfc3339(value: object) -> datetime:
    if not isinstance(value, str) or not _RFC3339_RE.fullmatch(value):
        raise ValidationError(f"invalid RFC 3339 timestamp {value!r}", code="bad_time")
    text = value.replace("Z", "+00:00")
    fraction = re.search(r"\.(\d+)", text)
    if fraction and len(fraction.group(1)) > 6:
        text = text.replace(fraction.group(0), "." + fraction.group(1)[:6])
    try:
        return datetime.fromisoformat(text).astimezone(UTC)
    except ValueError as exc:
        raise ValidationError(f"invalid RFC 3339 timestamp {value!r}", code="bad_time") from exc
