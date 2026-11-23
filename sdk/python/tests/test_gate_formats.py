"""Output gate and model format checks."""

from __future__ import annotations

import json
import struct
import zipfile
from pathlib import Path

import pytest

from depa.ccr.formats import check_format
from depa.ccr.gate import apply_gate, read_result
from depa.errors import ValidationError

RECIPE = {"outputs": [{"name": "model", "format": "safetensors"}]}
CONTRACT = {
    "output": {"allowed_formats": ["safetensors"], "max_bytes": 10_000, "metrics_allowlist": ["acc"]},
    "privacy": {"mode": "dp", "epsilon": 2.0, "delta": 1e-5},
}


def _safetensors(path: Path, payload: bytes = b"\x00" * 16) -> None:
    header = json.dumps({"w": {"dtype": "F32", "shape": [4], "data_offsets": [0, 16]}}).encode()
    path.write_bytes(struct.pack("<Q", len(header)) + header + payload)


def _result(**over) -> dict:
    base = {
        "status": "succeeded",
        "metrics": {"acc": 0.5, "x": 1, "nan": float("nan")},
        "privacy_spent": {"mode": "dp", "epsilon": 1.0, "delta": 1e-6, "accountant": "rdp"},
        "outputs": [{"name": "model", "format": "safetensors", "files": ["model.safetensors"]}],
    }
    base.update(over)
    return base


@pytest.fixture
def outdir(tmp_path: Path) -> Path:
    (tmp_path / "model").mkdir()
    _safetensors(tmp_path / "model" / "model.safetensors")
    return tmp_path


def test_gate_accepts_and_filters(outdir: Path) -> None:
    g = apply_gate(_result(), outputs_dir=outdir, recipe=RECIPE, contract=CONTRACT)
    assert g.metrics == {"acc": 0.5}
    assert g.outputs[0].files[0][0] == "model.safetensors" and g.total_bytes > 0
    (outdir / "model" / "config.json").write_text('{"a": 1}')
    g = apply_gate(
        _result(outputs=[{"name": "model", "format": "safetensors", "files": ["config.json", "model.safetensors"]}]),
        outputs_dir=outdir,
        recipe=RECIPE,
        contract=CONTRACT,
    )
    assert len(g.outputs[0].files) == 2
    with pytest.raises(ValidationError):  # metadata alone is not a model
        apply_gate(
            _result(outputs=[{"name": "model", "format": "safetensors", "files": ["config.json"]}]),
            outputs_dir=outdir,
            recipe=RECIPE,
            contract=CONTRACT,
        )


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"status": "failed"}, "trainer_failed"),
        ({"outputs": []}, "bad_result"),
        (
            {"outputs": [{"name": "other", "format": "safetensors", "files": ["model.safetensors"]}]},
            "undeclared_output",
        ),
        ({"outputs": [{"name": "model", "format": "onnx", "files": ["model.safetensors"]}]}, "format_not_allowed"),
        ({"outputs": [{"name": "model", "format": "safetensors", "files": ["../etc/passwd"]}]}, "bad_path"),
        ({"outputs": [{"name": "model", "format": "safetensors", "files": ["sub/x.safetensors"]}]}, "bad_result"),
        (
            {"outputs": [{"name": "model", "format": "safetensors", "files": ["missing.safetensors"]}]},
            "bad_output_file",
        ),
        (
            {"privacy_spent": {"mode": "dp", "epsilon": 3.0, "delta": 1e-6, "accountant": "rdp"}},
            "privacy_budget_exceeded",
        ),
        ({"privacy_spent": {"mode": "none"}}, "privacy"),
        ({"privacy_spent": {"mode": "dp", "epsilon": 1.0, "delta": 1e-6, "accountant": "a b"}}, "privacy"),
        ({"metrics": []}, "bad_result"),
    ],
)
def test_gate_rejections(outdir: Path, over: dict, code: str) -> None:
    with pytest.raises(ValidationError) as e:
        apply_gate(_result(**over), outputs_dir=outdir, recipe=RECIPE, contract=CONTRACT)
    assert e.value.code == code


def test_gate_size_limit_and_symlink(outdir: Path) -> None:
    _safetensors(outdir / "model" / "model.safetensors", b"\x00" * 16 + b"\x01" * 20_000)
    with pytest.raises(ValidationError) as e:
        apply_gate(_result(), outputs_dir=outdir, recipe=RECIPE, contract=CONTRACT)
    assert e.value.code == "outputs_too_large"
    (outdir / "model" / "model.safetensors").unlink()
    (outdir / "model" / "model.safetensors").symlink_to(outdir / "elsewhere")
    with pytest.raises(ValidationError):
        apply_gate(_result(), outputs_dir=outdir, recipe=RECIPE, contract=CONTRACT)


def test_privacy_none_contract(outdir: Path) -> None:
    contract = dict(CONTRACT, privacy={"mode": "none"})
    g = apply_gate(_result(privacy_spent=None), outputs_dir=outdir, recipe=RECIPE, contract=contract)
    assert g.privacy_spent == {"mode": "none"}
    for accountant in ("dp-xgboost:basic-composition+poisson-subsampling", "basic-composition(a,b)", "custom:x"):
        spent = {"mode": "dp", "epsilon": 1.0, "delta": 0.0, "accountant": accountant}
        assert (
            apply_gate(
                _result(privacy_spent=spent), outputs_dir=outdir, recipe=RECIPE, contract=CONTRACT
            ).privacy_spent["accountant"]
            == accountant
        )
    trainer_none = {"mode": "none", "epsilon": None, "delta": None, "accountant": "none"}  # depa_train shape
    g = apply_gate(_result(privacy_spent=trainer_none), outputs_dir=outdir, recipe=RECIPE, contract=contract)
    assert g.privacy_spent == {"mode": "none"}
    with pytest.raises(ValidationError):  # a DP contract never accepts mode none
        apply_gate(_result(privacy_spent=trainer_none), outputs_dir=outdir, recipe=RECIPE, contract=CONTRACT)


def test_read_result_limits(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        read_result(tmp_path / "absent.json")
    (tmp_path / "r.json").write_text("[1]")
    with pytest.raises(ValidationError):
        read_result(tmp_path / "r.json")
    (tmp_path / "big.json").write_bytes(b"{" + b" " * (2 << 20) + b"}")
    with pytest.raises(ValidationError):
        read_result(tmp_path / "big.json")


def test_formats(tmp_path: Path) -> None:
    p = tmp_path / "w.safetensors"
    _safetensors(p)
    check_format("safetensors", p)
    p.write_bytes(struct.pack("<Q", 10**9) + b"{}")
    with pytest.raises(ValidationError):
        check_format("safetensors", p)
    header = json.dumps({"w": {"dtype": "F32", "shape": [4], "data_offsets": [0, 999]}}).encode()
    p.write_bytes(struct.pack("<Q", len(header)) + header + b"\x00" * 16)
    with pytest.raises(ValidationError):
        check_format("safetensors", p)
    onnx = tmp_path / "m.onnx"
    onnx.write_bytes(b"\x08\x07rest")
    check_format("onnx", onnx)
    onnx.write_bytes(b"PK..")
    with pytest.raises(ValidationError):
        check_format("onnx", onnx)
    xgb = tmp_path / "m.json"
    xgb.write_text(json.dumps({"version": [2], "learner": {}}))
    check_format("xgboost-json", xgb)
    with pytest.raises(ValidationError):
        check_format("skops", xgb)  # a .json file is only metadata for skops
    for name, fmt in (("m.skops", "skops"), ("m.keras", "keras")):
        z = tmp_path / name
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("schema.json", "{}")
            zf.writestr("config.json", "{}")
            zf.writestr("metadata.json", "{}")
        check_format(fmt, z)
    with pytest.raises(ValidationError):
        check_format("pickle", tmp_path / "m.pkl")
    noext = tmp_path / "model"
    noext.write_bytes(b"x")
    with pytest.raises(ValidationError):
        check_format("onnx", noext)
