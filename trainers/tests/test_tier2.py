"""Tier 2: depa-graph/v1 export, op allowlist enforcement, safe loading and harness-owned training."""

from __future__ import annotations

import copy
import json

import pytest

from conftest import base_recipe, run_recipe, step, write_tabular_inputs
from depa_train.errors import SafetyError
from depa_train.tier2.ops import check_graph_document, check_op

torch = pytest.importorskip("torch")
pytest.importorskip("torchvision")  # torch/graph-train runs in the torch family image


class Net(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.fc1 = torch.nn.Linear(6, 16)
        self.norm = torch.nn.LayerNorm(16)
        self.drop = torch.nn.Dropout(0.1)
        self.fc2 = torch.nn.Linear(16, 1)

    def forward(self, x):
        return self.fc2(self.drop(torch.relu(self.norm(self.fc1(x)))))


@pytest.fixture
def graph_dir(tmp_path):
    from depa_train.tier2.export import export_graph

    out = tmp_path / "graph"
    torch.manual_seed(0)
    model = Net().train()
    assert export_graph(model, (torch.randn(4, 6),), out) == []
    return out


@pytest.mark.parametrize(
    ("op", "allowed"),
    [
        ("aten::addmm", True),
        ("aten::native_dropout", True),
        ("aten::sym_size.int", True),
        ("getitem", True),
        ("aten::add.out", False),
        ("aten::copy_", False),
        ("aten::empty.memory_format", False),
        ("aten::_local_scalar_dense", False),
        ("mylib::exfiltrate", False),
        ("torch.ops.higher_order.cond", False),
        ("builtins.eval", False),
    ],
)
def test_op_allowlist(op, allowed):
    assert (check_op(op) is None) == allowed


def test_exported_graph_document_is_valid(graph_dir):
    doc = json.loads((graph_dir / "graph.json").read_text())
    assert doc["format"] == "depa-graph/v1"
    assert check_graph_document(doc) == []
    assert {n["op"] for n in doc["nodes"]} <= {
        "aten::addmm",
        "aten::native_layer_norm",
        "aten::relu",
        "getitem",
        "aten::native_dropout",
        "aten::permute",
        "aten::sym_size.int",
        "aten::view",
    }


def _mutated(graph_dir, mutate):
    doc = json.loads((graph_dir / "graph.json").read_text())
    doc = copy.deepcopy(doc)
    mutate(doc)
    return check_graph_document(doc)


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda d: d["nodes"][-1].update(op="mylib::evil"), "custom namespaces are not allowed"),
        (lambda d: d["nodes"][-1].update(op="aten::copy_"), "not on the tier-2 allowlist"),
        (lambda d: d["nodes"][-1].update(op="aten::addmm.out"), "'out' overload"),
        (lambda d: d["nodes"][-1]["args"].append({"ref": "ghost"}), "undefined value 'ghost'"),
        (lambda d: d["nodes"][-1]["args"].append({"device": "cuda"}), "only the 'cpu' device"),
        (lambda d: d["nodes"][-1]["args"].append({"__reduce__": "os.system"}), "unknown argument encoding"),
        (lambda d: d["nodes"][-1]["kwargs"].update(out={"ref": "x"}), "kwarg 'out' not allowed"),
        (lambda d: d.update(format="torch.export"), "must be a 'depa-graph/v1' document"),
        (lambda d: d.update(code="import os"), "unknown keys"),
    ],
)
def test_graph_rejections(graph_dir, mutate, needle):
    errors = _mutated(graph_dir, mutate)
    assert any(needle in e for e in errors), errors


def test_loader_rejects_disallowed_graph_and_pickled_weights(graph_dir):
    from depa_train.tier2.build import build_graph_model

    model, _ = build_graph_model(graph_dir, 10_000)
    assert model(torch.randn(3, 6)).shape == (3, 1)
    doc = json.loads((graph_dir / "graph.json").read_text())
    original = doc["nodes"][0]["op"]
    doc["nodes"][0]["op"] = "mylib::evil"
    (graph_dir / "graph.json").write_text(json.dumps(doc))
    with pytest.raises(SafetyError, match="graph rejected"):
        build_graph_model(graph_dir, 10_000)
    doc["nodes"][0]["op"] = original
    (graph_dir / "graph.json").write_text(json.dumps(doc))
    with pytest.raises(SafetyError, match="max_parameters"):
        build_graph_model(graph_dir, 10)


def test_rebuilt_graph_matches_eager_model(tmp_path):
    from depa_train.tier2.build import build_graph_model
    from depa_train.tier2.export import export_graph

    torch.manual_seed(0)
    model = Net().eval()
    export_graph(model, (torch.randn(2, 6),), tmp_path / "g")
    rebuilt, _ = build_graph_model(tmp_path / "g", 10_000)
    rebuilt.eval()
    x = torch.randn(5, 6)
    assert torch.allclose(rebuilt(x), model(x), atol=1e-5)


def graph_recipe(privacy=None) -> dict:
    recipe = base_recipe(
        tier=2,
        family="torch",
        outputs=[{"name": "model", "format": "safetensors"}],
        metrics=["accuracy", "roc_auc", "train_loss"],
    )
    recipe["inputs"].append({"alias": "graph", "dataset_id": "tdc/graph"})
    split = step(recipe, "split")["with"]
    split.update({"scale": "minmax", "default_bounds": [-5, 5]})
    recipe["steps"][2] = {
        "id": "train",
        "uses": "torch/graph-train@1",
        "with": {
            "data": {"kind": "tabular", "step": "split"},
            "task": "binary",
            "graph": {"input": "graph"},
            "loss": {"class": "nn.BCEWithLogitsLoss"},
            "output_activation": "sigmoid",
            "training": {
                "epochs": 5,
                "batch_size": 32,
                "optimizer": {"name": "Adam", "params": {"lr": 0.01}},
                "max_grad_norm": 1.0,
            },
            "save": {"output": "model", "format": "safetensors"},
        },
    }
    if privacy:
        recipe["privacy"] = privacy
    return recipe


@pytest.mark.parametrize("privacy", [None, {"mode": "dp", "epsilon": 4.0, "delta": 1e-5}])
def test_graph_train_end_to_end(tmp_path, graph_dir, privacy):
    if privacy:
        pytest.importorskip("opacus")
    tabular_inputs = write_tabular_inputs(tmp_path / "data", n=4000)
    (tabular_inputs / "graph").mkdir()
    for f in graph_dir.iterdir():
        (tabular_inputs / "graph" / f.name).write_bytes(f.read_bytes())
    result = run_recipe(graph_recipe(privacy), tabular_inputs, tmp_path)
    assert result["status"] == "succeeded", result.get("error")
    assert result["outputs"][0]["files"] == ["graph.json", "weights.safetensors"]
    assert result["metrics"]["roc_auc"] > (0.55 if privacy else 0.8)
    if privacy:
        assert 3.5 < result["privacy_spent"]["epsilon"] <= 4.0
        assert result["privacy_spent"]["accountant"] == "opacus-rdp"


def test_graph_train_rejects_pickle_in_graph_dir(tmp_path, tabular_inputs, graph_dir):
    (tabular_inputs / "graph").mkdir()
    for f in graph_dir.iterdir():
        (tabular_inputs / "graph" / f.name).write_bytes(f.read_bytes())
    (tabular_inputs / "graph" / "extra.pt").write_bytes(b"\x80\x04.")
    result = run_recipe(graph_recipe(), tabular_inputs, tmp_path)
    assert result["status"] == "failed" and "refusing to load" in result["error"]
