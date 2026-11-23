"""Structured, allowlisted status lines (spec §12 step 6).

Only events named in ``EVENTS`` with fields named in ``FIELDS`` are ever written, and values
are restricted to short identifier-like strings, numbers and booleans. Free text (exception
messages, trainer output) is never logged, except on the ``virtual`` platform at ``debug``
level, which exists only for development.
"""

from __future__ import annotations

import json
import re
import sys
import time
from typing import IO

EVENTS = frozenset(
    {
        "agent.start",
        "agent.config",
        "contract.verified",
        "recipe.selected",
        "run.authorized",
        "rat.verified",
        "rat.refreshed",
        "dataset.manifest_verified",
        "dataset.keys_released",
        "dataset.decrypted",
        "dataset.skipped",
        "output.prepared",
        "trainer.start",
        "trainer.exit",
        "trainer.isolation",
        "gate.passed",
        "output.uploaded",
        "event.posted",
        "receipt.verified",
        "workdir.info",
        "agent.done",
        "agent.failed",
        "debug.detail",
    }
)
FIELDS = frozenset(
    {
        "contract_id",
        "run_id",
        "platform",
        "recipe_digest",
        "dataset_id",
        "version",
        "manifest_digest",
        "output",
        "format",
        "files",
        "bytes",
        "count",
        "seconds",
        "seq",
        "type",
        "txid",
        "code",
        "stage",
        "status",
        "returncode",
        "fstype",
        "network",
        "privdrop",
        "trust_bundle",
        "detail",
    }
)
_SAFE_STR = re.compile(r"^[A-Za-z0-9:._/#@+=-]{0,200}$")
_LEVELS = {"error": 40, "warn": 30, "info": 20, "debug": 10}


class StatusLog:
    def __init__(self, level: str = "info", *, allow_detail: bool = False, stream: IO[str] | None = None) -> None:
        self._threshold = _LEVELS.get(level, 20)
        self._allow_detail = allow_detail and level == "debug"
        self._stream = stream or sys.stderr

    def emit(self, event: str, level: str = "info", **fields: object) -> None:
        if event not in EVENTS or _LEVELS.get(level, 20) < self._threshold:
            return
        line: dict[str, object] = {"ts": round(time.time(), 3), "level": level, "event": event}
        for key, value in fields.items():
            if key not in FIELDS or value is None:
                continue
            if key == "detail":
                if self._allow_detail:
                    line[key] = str(value)[:2000]
                continue
            if isinstance(value, bool | int | float) or (isinstance(value, str) and _SAFE_STR.fullmatch(value)):
                line[key] = value
        self._stream.write(json.dumps(line, separators=(",", ":"), sort_keys=True) + "\n")
        self._stream.flush()

    def debug_detail(self, stage: str, detail: str) -> None:
        if self._allow_detail:
            self.emit("debug.detail", "debug", stage=stage, detail=detail)
