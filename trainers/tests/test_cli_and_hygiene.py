"""CLI behaviour and source-level hygiene rules (no pickle, no unsafe loaders, no remote code)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from conftest import base_recipe

SRC = Path(__file__).resolve().parents[1] / "src" / "depa_train"
CLI = [sys.executable, "-m", "depa_train.cli"]


def cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([*CLI, *args], capture_output=True, text=True, check=False)


def test_plugins_and_schema_commands():
    out = json.loads(cli("plugins", "--json").stdout)
    refs = {p["ref"] for p in out}
    assert {
        "sql/duckdb@1",
        "tabular/split@1",
        "sklearn/train@1",
        "xgboost/train@1",
        "torch/train@1",
        "keras/train@1",
        "hf/sft@1",
        "torch/graph-train@1",
        "custom/entrypoint@1",
        "eval/classification@1",
    } <= refs
    assert json.loads(cli("schema").stdout)["title"] == "DEPA Training recipe v1"
    assert json.loads(cli("schema", "sql/duckdb").stdout)["x-depa-plugin"]["name"] == "sql/duckdb"
    assert cli("schema", "nope/nope").returncode == 2


def test_validate_command(tmp_path):
    good = tmp_path / "r.yaml"
    good.write_text(yaml.safe_dump(base_recipe()))
    assert cli("validate", "--recipe", str(good)).stdout.strip() == "valid"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(base_recipe(tier=7)))
    proc = cli("validate", "--recipe", str(bad), "--json")
    assert proc.returncode == 2 and json.loads(proc.stdout)["valid"] is False
    contract = tmp_path / "c.json"
    contract.write_text(
        json.dumps(
            {
                "datasets": [],
                "computation": {"tier": 1, "custom_ccr": None},
                "privacy": {"mode": "none"},
                "output": {"allowed_formats": ["skops"]},
            }
        )
    )
    proc = cli("validate", "--recipe", str(good), "--contract", str(contract))
    assert proc.returncode == 2 and "not part of the contract" in proc.stdout


def test_run_command_with_guard(tmp_path, tabular_inputs):
    pytest.importorskip("skops")
    recipe = tmp_path / "r.json"
    recipe.write_text(json.dumps(base_recipe()))
    proc = subprocess.run(
        [
            "depa-train",
            "run",
            "--recipe",
            str(recipe),
            "--inputs",
            str(tabular_inputs),
            "--outputs",
            str(tmp_path / "out"),
            "--result",
            str(tmp_path / "result.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    result = json.loads((tmp_path / "result.json").read_text())
    assert proc.returncode == 0, result
    assert result["outputs"] == [{"name": "model", "format": "skops", "files": ["model.skops"]}]
    for line in proc.stderr.splitlines():
        if line.startswith("{"):
            assert set(json.loads(line)) <= {
                "ts",
                "event",
                "step",
                "plugin",
                "version",
                "seconds",
                "rows",
                "columns",
                "view",
                "features",
                "rows_train",
                "rows_val",
                "rows_test",
                "recipe",
                "tier",
                "steps",
            }


def test_run_command_invalid_recipe(tmp_path):
    recipe = tmp_path / "r.yaml"
    recipe.write_text("schema: nope\n")
    proc = cli(
        "run",
        "--recipe",
        str(recipe),
        "--inputs",
        str(tmp_path),
        "--outputs",
        str(tmp_path / "o"),
        "--result",
        str(tmp_path / "res.json"),
    )
    assert proc.returncode == 2
    assert json.loads((tmp_path / "res.json").read_text())["status"] == "failed"


FORBIDDEN = [
    (r"\bimport pickle\b|\bpickle\.load", "pickle"),
    (r"torch\.load\(", "torch.load"),
    (r"joblib\.load", "joblib.load"),
    (r"allow_pickle\s*=\s*True", "np.load(allow_pickle=True)"),
    (r"read_pickle", "pandas.read_pickle"),
    (r"trust_remote_code\s*=\s*True", "trust_remote_code=True"),
    (r"torch\.export\.load|torch\.jit\.load", "torch.export/jit.load"),
    (r"safe_mode\s*=\s*False|enable_unsafe_deserialization", "Keras unsafe deserialization"),
    (r"(?<![.\w])(eval|exec)\(", "eval/exec"),
    (r"shell\s*=\s*True", "shell=True"),
]


def test_source_hygiene():
    offenders = []
    for path in SRC.rglob("*.py"):
        text = path.read_text()
        for pattern, label in FORBIDDEN:
            if re.search(pattern, text):
                offenders.append(f"{path.relative_to(SRC)}: {label}")
    assert offenders == []
