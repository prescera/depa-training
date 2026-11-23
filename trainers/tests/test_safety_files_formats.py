"""Input file safety (no pickle formats, no symlinks, containment) and the output gate."""

from __future__ import annotations

import json
import struct
import zipfile

import pytest

from conftest import base_recipe, run_recipe, step
from depa_train.errors import SafetyError
from depa_train.formats import gate_outputs
from depa_train.recipe import OutputSpec
from depa_train.safety.files import reject_unsafe_files, resolve_files


@pytest.mark.parametrize(
    "name", ["data.pkl", "model.pt", "weights.pth", "x.joblib", "arr.npy", "pytorch_model.bin"]
)
def test_pickle_formats_are_never_read(tmp_path, name):
    (tmp_path / name).write_bytes(b"\x80\x04K\x01.")
    with pytest.raises(SafetyError, match="unsafe serialization format"):
        resolve_files(tmp_path, "*")
    with pytest.raises(SafetyError, match="refusing to load a directory"):
        reject_unsafe_files(tmp_path)


def test_symlinks_rejected(tmp_path):
    (tmp_path / "real.csv").write_text("a\n1\n")
    (tmp_path / "link.csv").symlink_to(tmp_path / "real.csv")
    with pytest.raises(SafetyError, match="symbolic links"):
        resolve_files(tmp_path, "*.csv")


def test_extension_must_match_format(tmp_path):
    (tmp_path / "data.parquet").write_bytes(b"PAR1")
    with pytest.raises(SafetyError, match="not accepted here"):
        resolve_files(tmp_path, "*", suffixes=frozenset({".csv"}))


def test_traversal_patterns_rejected_at_runtime(tmp_path):
    with pytest.raises(SafetyError, match="must be relative"):
        resolve_files(tmp_path, "../*")


def test_pickle_dropped_into_input_fails_run(tmp_path, tabular_inputs):
    pytest.importorskip("skops")
    (tabular_inputs / "left" / "extra.pkl").write_bytes(b"\x80\x04.")
    recipe = base_recipe()
    step(recipe, "join")["with"]["tables"][0]["files"] = "*"
    result = run_recipe(recipe, tabular_inputs, tmp_path)
    assert result["status"] == "failed"
    assert "unsafe serialization format" in result["error"]
    assert result["outputs"] == [] and not any((tmp_path / "outputs").iterdir())


def _safetensors_bytes(header: dict, data: bytes = b"") -> bytes:
    raw = json.dumps(header).encode()
    return struct.pack("<Q", len(raw)) + raw + data


def test_output_gate_accepts_valid_safetensors(tmp_path):
    (tmp_path / "model").mkdir()
    header = {"w": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}
    (tmp_path / "model" / "model.safetensors").write_bytes(_safetensors_bytes(header, b"\0\0\0\0"))
    (tmp_path / "model" / "config.json").write_text("{}")
    records = gate_outputs(tmp_path, [OutputSpec(name="model", format="safetensors")])
    assert records == [
        {"name": "model", "format": "safetensors", "files": ["config.json", "model.safetensors"]}
    ]


@pytest.mark.parametrize(
    ("files", "needle"),
    [
        ({"model.pkl": b"\x80\x04."}, "file type '.pkl' not allowed"),
        ({"model.safetensors": b"\xff" * 16}, "invalid safetensors header length"),
        (
            {
                "model.safetensors": _safetensors_bytes(
                    {"w": {"dtype": "F32", "shape": [1], "data_offsets": [0, 99]}}
                )
            },
            "offsets out of range",
        ),
        ({"config.json": b"{}"}, "contains no safetensors file"),
        ({".hidden.safetensors": b""}, "hidden files"),
    ],
)
def test_output_gate_rejections(tmp_path, files, needle):
    (tmp_path / "model").mkdir()
    for name, data in files.items():
        (tmp_path / "model" / name).write_bytes(data)
    with pytest.raises(SafetyError, match=needle):
        gate_outputs(tmp_path, [OutputSpec(name="model", format="safetensors")])


def test_output_gate_rejects_undeclared_entries(tmp_path):
    (tmp_path / "model").mkdir()
    (tmp_path / "leak.csv").write_text("secret")
    with pytest.raises(SafetyError, match=r"undeclared entry 'leak\.csv'"):
        gate_outputs(tmp_path, [OutputSpec(name="model", format="safetensors")])


def test_output_gate_checks_keras_archive_config(tmp_path):
    (tmp_path / "model").mkdir()
    config = {
        "module": "keras",
        "class_name": "Sequential",
        "config": {"layers": [{"module": "keras.layers", "class_name": "Lambda", "config": {}}]},
    }
    with zipfile.ZipFile(tmp_path / "model" / "model.keras", "w") as zf:
        zf.writestr("config.json", json.dumps(config))
        zf.writestr("model.weights.h5", b"")
    with pytest.raises(SafetyError, match="not allowlisted"):
        gate_outputs(tmp_path, [OutputSpec(name="model", format="keras")])


def test_hf_base_model_safety(tmp_path):
    pytest.importorskip("transformers")
    from types import SimpleNamespace

    from depa_train.plugins.hf_sft import SftConfig
    from depa_train.plugins.hf_sft.impl import _model_dir

    base = tmp_path / "inputs" / "base"
    base.mkdir(parents=True)
    (base / "tokenizer.json").write_text("{}")
    (base / "model.safetensors").write_bytes(b"")
    cfg = SftConfig.model_validate(
        {
            "base_model": {"input": "base"},
            "data": {"step": "x"},
            "format": {"kind": "text"},
            "lora": {"target_modules": ["q_proj"]},
            "save": {"output": "a", "format": "safetensors"},
        }
    )
    ctx = SimpleNamespace(input_root=lambda alias: base)
    for config, needle in [
        ({"model_type": "llama", "auto_map": {"AutoModel": "evil.Model"}}, "require remote code"),
        ({"model_type": "my_custom_arch"}, "not allowlisted"),
    ]:
        (base / "config.json").write_text(json.dumps(config))
        with pytest.raises(SafetyError, match=needle):
            _model_dir(ctx, cfg)
    (base / "config.json").write_text(json.dumps({"model_type": "llama"}))
    (base / "pytorch_model.bin").write_bytes(b"\x80\x04.")
    with pytest.raises(SafetyError, match="refusing to load"):
        _model_dir(ctx, cfg)
