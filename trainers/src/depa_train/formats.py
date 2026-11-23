"""Output formats and the output gate.

Every declared output is a flat directory ``<outputs>/<name>/`` holding at least one file in the
output's primary format plus optional JSON metadata. Files are checked structurally (headers,
archive members) so that nothing other than the declared, non-executable formats leaves the run.
"""

from __future__ import annotations

import json
import struct
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .errors import SafetyError
from .recipe import OutputSpec

MAX_JSON_BYTES = 64 << 20
MAX_SAFETENSORS_HEADER = 100 << 20


def _check_json(path: Path) -> object:
    if path.stat().st_size > MAX_JSON_BYTES:
        raise SafetyError(f"{path.name}: JSON file too large")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SafetyError(f"{path.name}: not valid UTF-8 JSON") from None


def _check_safetensors(path: Path) -> None:
    size = path.stat().st_size
    with path.open("rb") as fh:
        raw = fh.read(8)
        if len(raw) != 8:
            raise SafetyError(f"{path.name}: truncated safetensors file")
        (header_len,) = struct.unpack("<Q", raw)
        if header_len > MAX_SAFETENSORS_HEADER or 8 + header_len > size:
            raise SafetyError(f"{path.name}: invalid safetensors header length")
        try:
            header = json.loads(fh.read(header_len))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SafetyError(f"{path.name}: invalid safetensors header") from None
    if not isinstance(header, dict):
        raise SafetyError(f"{path.name}: invalid safetensors header")
    data_len = size - 8 - header_len
    for key, entry in header.items():
        if key == "__metadata__":
            if not isinstance(entry, dict) or not all(isinstance(v, str) for v in entry.values()):
                raise SafetyError(f"{path.name}: invalid safetensors metadata")
            continue
        if not isinstance(entry, dict) or set(entry) != {"dtype", "shape", "data_offsets"}:
            raise SafetyError(f"{path.name}: invalid safetensors tensor entry")
        start, end = entry["data_offsets"]
        if not (isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= data_len):
            raise SafetyError(f"{path.name}: safetensors offsets out of range")


def _check_onnx(path: Path) -> None:
    try:
        import onnx
    except ImportError:
        raise SafetyError("onnx is not installed; cannot verify ONNX output") from None
    try:
        model = onnx.load_model(str(path), load_external_data=False)
        onnx.checker.check_model(model)
    except Exception:
        raise SafetyError(f"{path.name}: not a valid self-contained ONNX model") from None
    for init in model.graph.initializer:
        if init.data_location == onnx.TensorProto.EXTERNAL:
            raise SafetyError(f"{path.name}: ONNX external data is not allowed")


def _check_xgboost_json(path: Path) -> None:
    doc = _check_json(path)
    if not isinstance(doc, dict) or "learner" not in doc:
        raise SafetyError(f"{path.name}: not an XGBoost JSON model")


def _check_ubj(path: Path) -> None:
    with path.open("rb") as fh:
        if fh.read(1) != b"{":
            raise SafetyError(f"{path.name}: not an XGBoost UBJSON model")


def _check_skops(path: Path) -> None:
    if not zipfile.is_zipfile(path):
        raise SafetyError(f"{path.name}: not a skops archive")
    with zipfile.ZipFile(path) as zf:
        if "schema.json" not in zf.namelist():
            raise SafetyError(f"{path.name}: skops archive without schema.json")
    try:
        import skops.io as sio
    except ImportError:
        raise SafetyError("skops is not installed; cannot verify skops output") from None
    untrusted = sio.get_untrusted_types(file=str(path))
    if untrusted:
        raise SafetyError(f"{path.name}: skops archive references untrusted types {sorted(untrusted)}")


def _check_keras(path: Path) -> None:
    from .safety.keras_config import check_keras_config

    if not zipfile.is_zipfile(path):
        raise SafetyError(f"{path.name}: not a .keras archive")
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        if not {"config.json", "model.weights.h5"} <= names or not names <= {
            "config.json",
            "model.weights.h5",
            "metadata.json",
        }:
            raise SafetyError(f"{path.name}: unexpected .keras archive members")
        try:
            config = json.loads(zf.read("config.json"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SafetyError(f"{path.name}: invalid .keras config") from None
    errors = check_keras_config(config)
    if errors:
        raise SafetyError(f"{path.name}: .keras config is not allowlisted: {errors[0]}")


@dataclass(frozen=True)
class FormatSpec:
    primary_suffix: str
    checks: dict[str, Callable[[Path], object]]


FORMATS: dict[str, FormatSpec] = {
    "safetensors": FormatSpec(".safetensors", {".safetensors": _check_safetensors, ".json": _check_json}),
    "onnx": FormatSpec(".onnx", {".onnx": _check_onnx, ".json": _check_json}),
    "xgboost-json": FormatSpec(".json", {".json": _check_json}),
    "xgboost-ubj": FormatSpec(".ubj", {".ubj": _check_ubj, ".json": _check_json}),
    "skops": FormatSpec(".skops", {".skops": _check_skops, ".json": _check_json}),
    "keras": FormatSpec(".keras", {".keras": _check_keras, ".json": _check_json}),
}


def gate_outputs(outputs_dir: Path, declared: list[OutputSpec]) -> list[dict[str, object]]:
    """Verify the outputs directory and return the ``outputs`` section of ``result.json``."""
    names = {o.name for o in declared}
    for entry in outputs_dir.iterdir():
        if entry.name not in names or entry.is_symlink() or not entry.is_dir():
            raise SafetyError(f"undeclared entry '{entry.name}' in outputs directory")
    records = []
    for out in declared:
        spec = FORMATS[out.format]
        out_dir = outputs_dir / out.name
        files: list[str] = []
        primary = 0
        for path in sorted(out_dir.iterdir()) if out_dir.is_dir() else []:
            if path.is_symlink() or not path.is_file():
                raise SafetyError(f"output '{out.name}': only regular files are allowed")
            if path.name.startswith("."):
                raise SafetyError(f"output '{out.name}': hidden files are not allowed")
            suffix = path.suffix.lower()
            check = spec.checks.get(suffix)
            if check is None:
                raise SafetyError(f"output '{out.name}': file type '{suffix}' not allowed for {out.format}")
            check(path)
            if suffix == spec.primary_suffix:
                primary += 1
            files.append(path.name)
        if out.format == "xgboost-json":
            for name in files:
                _check_xgboost_json(out_dir / name)
        if primary == 0:
            raise SafetyError(f"output '{out.name}' contains no {out.format} file")
        records.append({"name": out.name, "format": out.format, "files": files})
    return records
