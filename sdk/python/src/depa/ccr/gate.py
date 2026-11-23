"""Output gate (spec §12 step 5): decide exactly what may leave the clean room.

Only outputs declared by the recipe, in formats the contract allows and that pass structural
checks, from regular files inside the single output directory, within ``max_bytes`` in total;
metrics filtered to ``metrics_allowlist`` and restricted to finite numbers; privacy spent
within the contract. Anything else fails the run (fail closed).
"""

from __future__ import annotations

import math
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeGuard

from depa.ccr.formats import check_output_file
from depa.core.dedf import validate_relpath
from depa.core.encoding import loads_object
from depa.errors import DepaError, ValidationError

MAX_RESULT_BYTES = 1 << 20
MAX_METRICS = 64
MAX_FILES_PER_OUTPUT = 256
_ACCOUNTANT_RE = re.compile(
    r"^[a-z0-9][a-z0-9_.:+(),<>-]{0,127}$"
)  # e.g. dp-xgboost:basic-composition+poisson-subsampling


@dataclass(frozen=True)
class GatedOutput:
    name: str
    format: str
    files: tuple[tuple[str, Path], ...]  # (relative path inside the output, absolute path)
    size: int


@dataclass(frozen=True)
class GateResult:
    outputs: tuple[GatedOutput, ...]
    metrics: dict[str, float | int]
    privacy_spent: dict[str, Any]
    total_bytes: int


def _fail(message: str, code: str = "gate_rejected") -> ValidationError:
    return ValidationError(message, code=code)


def read_result(path: Path) -> dict[str, Any]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise _fail("trainer produced no result.json", "no_result") from exc
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise _fail("result.json is not a regular file", "bad_result")
        data = f.read(MAX_RESULT_BYTES + 1)
    if len(data) > MAX_RESULT_BYTES:
        raise _fail("result.json too large", "bad_result")
    try:
        return loads_object(data)
    except DepaError as exc:
        raise _fail("result.json is not a JSON object", "bad_result") from exc


def _finite(value: object) -> TypeGuard[float]:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _privacy(spent: object, contract_privacy: Mapping[str, Any]) -> dict[str, Any]:
    if contract_privacy["mode"] == "none" and (
        spent is None or (isinstance(spent, dict) and spent.get("mode") == "none")
    ):
        return {"mode": "none"}
    if not isinstance(spent, dict) or spent.get("mode") != "dp":
        raise _fail("privacy_spent must report mode dp", "privacy")
    eps, delta, accountant = spent.get("epsilon"), spent.get("delta"), spent.get("accountant")
    if not _finite(eps) or not _finite(delta) or eps <= 0 or not 0 <= delta < 1:
        raise _fail("privacy_spent epsilon/delta are malformed", "privacy")
    if not isinstance(accountant, str) or not _ACCOUNTANT_RE.fullmatch(accountant):
        raise _fail("privacy_spent.accountant is malformed", "privacy")
    if contract_privacy["mode"] == "dp" and (eps > contract_privacy["epsilon"] or delta > contract_privacy["delta"]):
        raise _fail("privacy budget exceeded", "privacy_budget_exceeded")
    return {"mode": "dp", "epsilon": eps, "delta": delta, "accountant": accountant}


def apply_gate(
    result: Mapping[str, Any],
    *,
    outputs_dir: Path,
    recipe: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> GateResult:
    if result.get("status") != "succeeded":
        raise _fail("trainer did not report success", "trainer_failed")
    output_cfg = contract["output"]
    declared = {o["name"]: o["format"] for o in recipe["outputs"]}
    allowed_formats = set(output_cfg["allowed_formats"])
    root = outputs_dir.resolve()
    raw_outputs = result.get("outputs")
    if not isinstance(raw_outputs, list) or not raw_outputs:
        raise _fail("result.outputs must be a non-empty list", "bad_result")
    seen_names: set[str] = set()
    seen_files: set[str] = set()
    gated: list[GatedOutput] = []
    total = 0
    for item in raw_outputs:
        if not isinstance(item, dict) or not isinstance(item.get("files"), list):
            raise _fail("malformed result output entry", "bad_result")
        name, fmt = item.get("name"), item.get("format")
        if not isinstance(name, str) or name not in declared or name in seen_names:
            raise _fail("result output is undeclared or duplicated", "undeclared_output")
        seen_names.add(name)
        if fmt != declared[name] or fmt not in allowed_formats:
            raise _fail(f"output {name} format is not the declared/allowed one", "format_not_allowed")
        files = item["files"]
        if not files or len(files) > MAX_FILES_PER_OUTPUT:
            raise _fail(f"output {name} has no or too many files", "bad_result")
        # Trainer convention: files are plain names inside <outputs>/<name>/ (no subdirectories).
        out_dir = root / name
        if out_dir.is_symlink() or not out_dir.is_dir():
            raise _fail(f"output directory {name} missing or not a directory", "bad_output_file")
        entries: list[tuple[str, Path]] = []
        size, primaries = 0, 0
        for item_path in files:
            rel = validate_relpath(item_path)
            if "/" in rel or rel.startswith(".") or rel in seen_files:
                raise _fail("output file names must be unique, flat and not hidden", "bad_result")
            seen_files.add(rel)
            path = out_dir / rel
            st = path.lstat() if (path.exists() or path.is_symlink()) else None
            if st is None or not stat.S_ISREG(st.st_mode):
                raise _fail("output file missing or not a regular file", "bad_output_file")
            primaries += check_output_file(str(fmt), path)
            size += st.st_size
            entries.append((rel, path))
        if primaries == 0:
            raise _fail(f"output {name} contains no {fmt} model file", "bad_output_format")
        total += size
        gated.append(GatedOutput(name, str(fmt), tuple(entries), size))
    missing = set(declared) - seen_names
    if missing:
        raise _fail(f"declared outputs missing: {sorted(missing)}", "missing_output")
    if total > output_cfg["max_bytes"]:
        raise _fail("outputs exceed output.max_bytes", "outputs_too_large")
    raw_metrics = result.get("metrics", {})
    if not isinstance(raw_metrics, dict):
        raise _fail("result.metrics must be an object", "bad_result")
    allow = set(output_cfg["metrics_allowlist"])
    metrics = {k: v for k, v in raw_metrics.items() if k in allow and _finite(v)}
    if len(metrics) > MAX_METRICS:
        raise _fail("too many metrics", "bad_result")
    privacy = _privacy(result.get("privacy_spent"), contract["privacy"])
    return GateResult(tuple(gated), metrics, privacy, total)
