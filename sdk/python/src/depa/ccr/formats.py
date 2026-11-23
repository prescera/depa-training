"""Structural checks for exportable model formats (output gate, spec §12 step 5).

These checks ensure a declared output really is a file of its format (not arbitrary bytes
labelled as a model). They cannot prove a model does not memorise data; that is what DP and
the recipe/release review are for. Unknown formats are rejected.
"""

from __future__ import annotations

import struct
import zipfile
from collections.abc import Callable
from pathlib import Path

from depa.core.encoding import loads_strict
from depa.errors import DepaError, ValidationError

MAX_SAFETENSORS_HEADER = 100 << 20
MAX_JSON_MODEL = 2 << 30


def _bad(fmt: str, path: Path, why: str) -> ValidationError:
    return ValidationError(f"{path.name} is not valid {fmt}: {why}", code="bad_output_format")


def check_safetensors(path: Path) -> None:
    size = path.stat().st_size
    with path.open("rb") as f:
        head = f.read(8)
        if len(head) != 8:
            raise _bad("safetensors", path, "truncated")
        (n,) = struct.unpack("<Q", head)
        if n == 0 or n > MAX_SAFETENSORS_HEADER or 8 + n > size:
            raise _bad("safetensors", path, "bad header length")
        try:
            header = loads_strict(f.read(n))
        except DepaError as exc:
            raise _bad("safetensors", path, "header is not JSON") from exc
    if not isinstance(header, dict):
        raise _bad("safetensors", path, "header is not an object")
    data_len = size - 8 - n
    for name, info in header.items():
        if name == "__metadata__":
            if not isinstance(info, dict) or not all(isinstance(v, str) for v in info.values()):
                raise _bad("safetensors", path, "bad __metadata__")
            continue
        offsets = info.get("data_offsets") if isinstance(info, dict) else None
        if (
            not isinstance(offsets, list)
            or len(offsets) != 2
            or not all(isinstance(o, int) and not isinstance(o, bool) for o in offsets)
            or not 0 <= offsets[0] <= offsets[1] <= data_len
            or not isinstance(info.get("dtype"), str)
            or not isinstance(info.get("shape"), list)
        ):
            raise _bad("safetensors", path, f"bad tensor entry {name!r}")


def check_onnx(path: Path) -> None:
    """ONNX ModelProto starts with field 1 (``ir_version``, varint): tag byte 0x08."""
    with path.open("rb") as f:
        head = f.read(2)
    if len(head) < 2 or head[0] != 0x08 or head[1] == 0:
        raise _bad("onnx", path, "not an ONNX ModelProto")


def check_xgboost_json(path: Path) -> None:
    if path.stat().st_size > MAX_JSON_MODEL:
        raise _bad("xgboost-json", path, "too large")
    try:
        doc = loads_strict(path.read_bytes())
    except DepaError as exc:
        raise _bad("xgboost-json", path, "not JSON") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("learner"), dict) or "version" not in doc:
        raise _bad("xgboost-json", path, "missing learner/version")


def _zip_with(path: Path, fmt: str, required: tuple[str, ...]) -> None:
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            bad = zf.testzip()
    except (zipfile.BadZipFile, OSError) as exc:
        raise _bad(fmt, path, "not a zip archive") from exc
    if bad is not None:
        raise _bad(fmt, path, "corrupt zip member")
    missing = [r for r in required if r not in names]
    if missing:
        raise _bad(fmt, path, f"missing {missing}")


def check_skops(path: Path) -> None:
    _zip_with(path, "skops", ("schema.json",))


def check_keras(path: Path) -> None:
    _zip_with(path, "keras", ("config.json", "metadata.json"))


VALIDATORS: dict[str, Callable[[Path], None]] = {
    "safetensors": check_safetensors,
    "onnx": check_onnx,
    "xgboost-json": check_xgboost_json,
    "skops": check_skops,
    "keras": check_keras,
}


PRIMARY_SUFFIX: dict[str, str] = {
    "safetensors": ".safetensors",
    "onnx": ".onnx",
    "xgboost-json": ".json",
    "skops": ".skops",
    "keras": ".keras",
}
MAX_SIDECAR_JSON = 1 << 20


def _check_sidecar_json(path: Path) -> None:
    """Metadata next to a model (e.g. ``config.json``): a small strict JSON object."""
    if path.stat().st_size > MAX_SIDECAR_JSON:
        raise _bad("json", path, "metadata file too large")
    try:
        doc = loads_strict(path.read_bytes())
    except DepaError as exc:
        raise _bad("json", path, "not JSON") from exc
    if not isinstance(doc, dict):
        raise _bad("json", path, "metadata must be a JSON object")


def check_output_file(fmt: str, path: Path) -> bool:
    """Validate one file of an output (the trainer's convention: the format's primary file(s)
    plus optional ``.json`` metadata). Returns whether it is a primary model file."""
    validator, primary = VALIDATORS.get(fmt), PRIMARY_SUFFIX.get(fmt)
    if validator is None or primary is None:
        raise ValidationError(
            f"output format {fmt!r} has no validator and cannot be exported", code="bad_output_format"
        )
    suffix = path.suffix.lower()
    if suffix == primary:
        validator(path)
        return True
    if suffix == ".json":
        _check_sidecar_json(path)
        return False
    raise _bad(fmt, path, f"file type {suffix or '(none)'} is not allowed")


def check_format(fmt: str, path: Path) -> None:
    """Validate a primary model file of ``fmt``."""
    if not check_output_file(fmt, path):
        raise _bad(fmt, path, "not a primary model file")
