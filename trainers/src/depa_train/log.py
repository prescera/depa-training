"""Structured status lines on stderr. Callers pass identifiers and numbers only, never data values."""

from __future__ import annotations

import json
import sys
import time
from numbers import Real

_ALLOWED = (str, bool, int, float, type(None))


def status(event: str, **fields: object) -> None:
    record: dict[str, object] = {"ts": round(time.time(), 3), "event": event}
    for key, raw in fields.items():
        value = round(float(raw), 6) if isinstance(raw, Real) and not isinstance(raw, bool | int) else raw
        record[key] = value if isinstance(value, _ALLOWED) else type(value).__name__
    sys.stderr.write(json.dumps(record, sort_keys=True) + "\n")
    sys.stderr.flush()
