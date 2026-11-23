"""Small end-to-end runs of every plugin through the executor (each skips if its family is absent)."""

from __future__ import annotations

import json
import sys
import textwrap

import numpy as np
import pytest

from conftest import base_recipe, run_recipe, step

# ----------------------------------------------------------------------------------- classic family


def test_sql_split_sklearn_eval(tmp_path, tabular_inputs):
    pytest.importorskip("skops")
    result = run_recipe(base_recipe(), tabular_inputs, tmp_path)
    assert result["status"] == "succeeded", result.get("error")
    assert result["metrics"]["accuracy"] > 0.75 and result["metrics"]["roc_auc"] > 0.8
    assert result["privacy_spent"]["mode"] == "none"
    import skops.io as sio

    model = sio.load(tmp_path / "outputs" / "model" / "model.skops", trusted=[])
    assert hasattr(model, "predict")


def test_sklearn_regression_with_pipeline(tmp_path, tabular_inputs):
    pytest.importorskip("skops")
    recipe = base_recipe(metrics=["r2", "rmse"])
    split = step(recipe, "split")["with"]
    split.update({"target": "x1", "task": "regression", "features": ["x2", "x3", "label"]})
    del split["classes"], split["categorical"]
    step(recipe, "train")["with"].update(
        estimator={"class": "sklearn.linear_model.Ridge", "params": {"alpha": 0.1}},
        preprocess=[{"class": "sklearn.preprocessing.StandardScaler"}],
    )
    step(recipe, "evaluate").update(
        uses="eval/regression@1", **{"with": {"predictions": {"step": "train"}, "metrics": ["r2", "rmse"]}}
    )
    result = run_recipe(recipe, tabular_inputs, tmp_path)
    assert result["status"] == "succeeded", result.get("error")
    assert result["metrics"]["rmse"] > 0


@pytest.mark.parametrize("fmt", ["xgboost-json", "xgboost-ubj"])
def test_xgboost_plain(tmp_path, tabular_inputs, fmt):
    xgb = pytest.importorskip("xgboost")
    recipe = base_recipe(outputs=[{"name": "model", "format": fmt}])
    step(recipe, "train").update(
        uses="xgboost/train@1",
        **{
            "with": {
                "data": {"step": "split"},
                "num_boost_round": 30,
                "params": {"max_depth": 3},
                "save": {"output": "model", "format": fmt},
            }
        },
    )
    result = run_recipe(recipe, tabular_inputs, tmp_path)
    assert result["status"] == "succeeded", result.get("error")
    assert result["metrics"]["roc_auc"] > 0.8
    booster = xgb.Booster()
    booster.load_model(str(tmp_path / "outputs" / "model" / result["outputs"][0]["files"][0]))


def test_dp_xgboost_model_loads_in_upstream_xgboost(tmp_path, tabular_inputs):
    pytest.importorskip("dp_xgboost")
    xgb = pytest.importorskip("xgboost")
    recipe = base_recipe(
        privacy={"mode": "dp", "epsilon": 5.0, "delta": 1e-5},
        outputs=[{"name": "model", "format": "xgboost-json"}],
    )
    step(recipe, "train").update(
        uses="xgboost/train@1",
        **{
            "with": {
                "data": {"step": "split"},
                "num_boost_round": 10,
                "save": {"output": "model", "format": "xgboost-json"},
            }
        },
    )
    result = run_recipe(recipe, tabular_inputs, tmp_path)
    assert result["status"] == "succeeded", result.get("error")
    booster = xgb.Booster()
    booster.load_model(str(tmp_path / "outputs" / "model" / "model.json"))
    assert booster.predict(xgb.DMatrix(np.zeros((2, 6), dtype=np.float32))).shape == (2,)


# ------------------------------------------------------------------------------------- torch family

MLP = {
    "kind": "factory",
    "config": {
        "layers": {
            "fc1": {"class": "nn.Linear", "params": {"in_features": 6, "out_features": 16}},
            "fc2": {"class": "nn.Linear", "params": {"in_features": 16, "out_features": 1}},
        },
        "input": ["x"],
        "forward": [{"ops": ["fc1", "F.relu", "fc2"], "input": ["x"], "output": "y"}],
        "output": ["y"],
    },
}


def torch_recipe(fmt="safetensors", **train_overrides) -> dict:
    recipe = base_recipe(
        family="torch",
        outputs=[{"name": "model", "format": fmt}],
        metrics=["accuracy", "roc_auc", "train_loss", "test_loss"],
    )
    step(recipe, "split")["with"].update({"scale": "standard"})
    train = {
        "data": {"kind": "tabular", "step": "split"},
        "task": "binary",
        "model": MLP,
        "loss": {"class": "nn.BCEWithLogitsLoss"},
        "output_activation": "sigmoid",
        "training": {
            "epochs": 8,
            "batch_size": 32,
            "optimizer": {"name": "Adam", "params": {"lr": 0.01}},
            "scheduler": {"name": "StepLR", "params": {"step_size": 4}},
        },
        "save": {"output": "model", "format": fmt},
    }
    train.update(train_overrides)
    recipe["steps"][2] = {"id": "train", "uses": "torch/train@1", "with": train}
    return recipe


@pytest.mark.parametrize(
    ("fmt", "backend"), [("safetensors", "native"), ("onnx", "native"), ("safetensors", "lightning")]
)
def test_torch_train(tmp_path, tabular_inputs, fmt, backend):
    pytest.importorskip("torchvision")  # torch family image
    if backend == "lightning":
        pytest.importorskip("lightning")
    if fmt == "onnx":
        pytest.importorskip("onnxscript")
    result = run_recipe(torch_recipe(fmt, backend=backend), tabular_inputs, tmp_path)
    assert result["status"] == "succeeded", result.get("error")
    assert result["metrics"]["roc_auc"] > 0.8


def test_torch_init_weights_from_input(tmp_path, tabular_inputs):
    pytest.importorskip("torchvision")  # torch family image
    from safetensors.torch import save_file

    from depa_train.torchkit.model_factory import build_factory_model

    (tabular_inputs / "init").mkdir()
    save_file(build_factory_model(MLP["config"]).state_dict(), str(tabular_inputs / "init" / "w.safetensors"))
    recipe = torch_recipe(init={"input": "init", "file": "w.safetensors"})
    recipe["inputs"].append({"alias": "init", "dataset_id": "tdc/init-weights"})
    assert run_recipe(recipe, tabular_inputs, tmp_path)["status"] == "succeeded"


def _png_cases(root, n=24, size=32):
    from PIL import Image

    rng = np.random.default_rng(0)
    for i in range(n):
        case = root / f"case_{i:03d}"
        case.mkdir(parents=True)
        mask = np.zeros((size, size), np.uint8)
        r, c = rng.integers(4, size - 12, 2)
        mask[r : r + 8, c : c + 8] = 255
        img = np.clip(rng.normal(60, 20, (size, size)) + (mask > 0) * 120, 0, 255).astype(np.uint8)
        Image.fromarray(img).save(case / f"case_{i:03d}_flair.png")
        Image.fromarray(mask).save(case / f"case_{i:03d}_seg.png")


def test_torch_segmentation_monai_unet(tmp_path):
    pytest.importorskip("monai")
    pytest.importorskip("PIL")
    inputs = tmp_path / "inputs"
    _png_cases(inputs / "hosp_a")
    recipe = {
        "schema": "depa-training/recipe/v1",
        "name": "seg",
        "tier": 1,
        "family": "torch",
        "inputs": [{"alias": "hosp_a", "dataset_id": "hosp-a/mri"}],
        "steps": [
            {
                "id": "train",
                "uses": "torch/train@1",
                "with": {
                    "data": {
                        "kind": "image_pairs",
                        "inputs": ["hosp_a"],
                        "samples": "case_*",
                        "image": "*_flair.png",
                        "mask": "*_seg.png",
                        "splits": {"train": 0.75, "test": 0.25},
                    },
                    "task": "segmentation",
                    "model": {
                        "kind": "arch",
                        "name": "monai.UNet",
                        "params": {
                            "spatial_dims": 2,
                            "in_channels": 1,
                            "out_channels": 1,
                            "channels": [8, 16],
                            "strides": [2],
                            "norm": ["group", {"num_groups": 4}],
                        },
                    },
                    "loss": {"class": "monai.losses.DiceLoss", "params": {"sigmoid": True}},
                    "training": {
                        "epochs": 3,
                        "batch_size": 6,
                        "optimizer": {"name": "Adam", "params": {"lr": 0.01}},
                    },
                    "output_activation": "sigmoid",
                    "save": {"output": "model", "format": "safetensors"},
                },
            },
            {
                "id": "evaluate",
                "uses": "eval/segmentation@1",
                "with": {
                    "predictions": {"step": "train"},
                    "metrics": ["dice_score", "jaccard_index", "hausdorff_distance"],
                },
            },
        ],
        "privacy": {"mode": "none"},
        "outputs": [{"name": "model", "format": "safetensors"}],
        "metrics": ["dice_score", "jaccard_index"],
    }
    result = run_recipe(recipe, inputs, tmp_path)
    assert result["status"] == "succeeded", result.get("error")
    assert 0 <= result["metrics"]["dice_score"] <= 1


@pytest.mark.parametrize("fmt", ["safetensors", "hdf5"])
def test_torch_tensor_file_multiclass(tmp_path, fmt):
    pytest.importorskip("torchvision")  # torch family image
    rng = np.random.default_rng(1)
    x = rng.normal(size=(1000, 1, 8, 8)).astype(np.float32)
    y = (x[:, 0, :4].mean(axis=(1, 2)) > 0).astype(np.int64) + (x[:, 0, 4:].mean(axis=(1, 2)) > 0).astype(
        np.int64
    )
    root = tmp_path / "inputs" / "imgs"
    root.mkdir(parents=True)
    if fmt == "safetensors":
        from safetensors.numpy import save_file

        save_file({"features": x, "targets": y}, str(root / "d.safetensors"))
        name = "d.safetensors"
    else:
        import h5py

        with h5py.File(root / "d.h5", "w") as fh:
            fh["features"], fh["targets"] = x, y
        name = "d.h5"
    recipe = {
        "schema": "depa-training/recipe/v1",
        "name": "mc",
        "tier": 1,
        "family": "torch",
        "inputs": [{"alias": "imgs", "dataset_id": "pr/imgs"}],
        "steps": [
            {
                "id": "train",
                "uses": "torch/train@1",
                "with": {
                    "data": {
                        "kind": "tensors",
                        "input": "imgs",
                        "file": name,
                        "format": fmt,
                        "splits": {"train": 0.8, "test": 0.2},
                    },
                    "task": "multiclass",
                    "model": {
                        "kind": "factory",
                        "config": {
                            "layers": {
                                "fc": {"class": "nn.Linear", "params": {"in_features": 64, "out_features": 3}}
                            },
                            "input": ["x"],
                            "forward": [
                                {
                                    "ops": [["torch.flatten", {"start_dim": 1}], "fc"],
                                    "input": ["x"],
                                    "output": "y",
                                }
                            ],
                            "output": ["y"],
                        },
                    },
                    "loss": {"class": "nn.CrossEntropyLoss"},
                    "output_activation": "softmax",
                    "training": {
                        "epochs": 10,
                        "batch_size": 32,
                        "optimizer": {"name": "Adam", "params": {"lr": 0.02}},
                    },
                    "save": {"output": "model", "format": "safetensors"},
                },
            },
            {
                "id": "evaluate",
                "uses": "eval/classification@1",
                "with": {
                    "predictions": {"step": "train"},
                    "metrics": ["accuracy", "f1_score", "roc_auc", "log_loss"],
                },
            },
        ],
        "privacy": {"mode": "none"},
        "outputs": [{"name": "model", "format": "safetensors"}],
        "metrics": ["accuracy", "roc_auc"],
    }
    result = run_recipe(recipe, tmp_path / "inputs", tmp_path)
    assert result["status"] == "succeeded", result.get("error")
    assert result["metrics"]["accuracy"] > 0.5  # chance level is ~0.33


def test_keras_train(tmp_path, tabular_inputs):
    pytest.importorskip("keras")
    model = {
        "module": "keras",
        "class_name": "Sequential",
        "config": {
            "name": "m",
            "layers": [
                {"module": "keras.layers", "class_name": "InputLayer", "config": {"batch_shape": [None, 6]}},
                {
                    "module": "keras.layers",
                    "class_name": "Dense",
                    "config": {"units": 8, "activation": "relu"},
                },
                {
                    "module": "keras.layers",
                    "class_name": "Dense",
                    "config": {"units": 1, "activation": "sigmoid"},
                },
            ],
        },
    }
    recipe = torch_recipe("keras")
    recipe["steps"][2] = {
        "id": "train",
        "uses": "keras/train@1",
        "with": {
            "data": {"kind": "tabular", "step": "split"},
            "task": "binary",
            "model": model,
            "loss": "binary_crossentropy",
            "optimizer": {"name": "adam", "params": {"learning_rate": 0.02}},
            "epochs": 10,
            "batch_size": 32,
            "save": {"output": "model", "format": "keras"},
        },
    }
    result = run_recipe(recipe, tabular_inputs, tmp_path)
    assert result["status"] == "succeeded", result.get("error")
    assert result["metrics"]["roc_auc"] > 0.8
    import keras

    loaded = keras.saving.load_model(tmp_path / "outputs" / "model" / "model.keras", safe_mode=True)
    assert loaded.count_params() == 6 * 8 + 8 + 8 + 1


# --------------------------------------------------------------------------------------- llm family


@pytest.fixture
def tiny_llm_inputs(tmp_path):
    pytest.importorskip("transformers")
    pytest.importorskip("peft")
    import torch
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers
    from transformers import LlamaConfig, LlamaForCausalLM, PreTrainedTokenizerFast

    inputs = tmp_path / "inputs"
    rows = [{"prompt": f"add {a} and {b}", "completion": str(a + b)} for a in range(12) for b in range(12)]
    tok = Tokenizer(models.BPE(unk_token="<unk>"))
    tok.pre_tokenizer, tok.decoder = pre_tokenizers.ByteLevel(), decoders.ByteLevel()
    tok.train_from_iterator(
        [f"Q: {r['prompt']} A: {r['completion']}" for r in rows],
        trainers.BpeTrainer(
            vocab_size=300,
            special_tokens=["<unk>", "<s>", "</s>", "<pad>"],
            initial_alphabet=pre_tokenizers.ByteLevel.alphabet(),
        ),
    )
    fast = PreTrainedTokenizerFast(
        tokenizer_object=tok, bos_token="<s>", eos_token="</s>", unk_token="<unk>", pad_token="<pad>"
    )
    config = LlamaConfig(
        vocab_size=len(fast),
        hidden_size=32,
        intermediate_size=64,
        num_hidden_layers=2,
        num_attention_heads=4,
        num_key_value_heads=2,
        max_position_embeddings=64,
        bos_token_id=1,
        eos_token_id=2,
        pad_token_id=3,
    )
    torch.manual_seed(0)
    LlamaForCausalLM(config).save_pretrained(inputs / "base", safe_serialization=True)
    fast.save_pretrained(inputs / "base")
    (inputs / "data").mkdir()
    (inputs / "data" / "d.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return inputs


def sft_recipe(privacy=None) -> dict:
    return {
        "schema": "depa-training/recipe/v1",
        "name": "sft",
        "tier": 1,
        "family": "llm",
        "inputs": [{"alias": "base", "dataset_id": "tdc/base"}, {"alias": "data", "dataset_id": "tdp/data"}],
        "steps": [
            {
                "id": "sft",
                "uses": "hf/sft@1",
                "with": {
                    "base_model": {"input": "base"},
                    "data": {"input": "data", "files": "*.jsonl", "format": "jsonl"},
                    "format": {"kind": "prompt_completion", "template": "Q: {prompt} A:"},
                    "max_seq_len": 32,
                    "lora": {"r": 4, "alpha": 8, "target_modules": ["q_proj", "v_proj"]},
                    "training": {"epochs": 2, "batch_size": 16, "learning_rate": 0.005, "max_grad_norm": 1.0},
                    "save": {"output": "adapter", "format": "safetensors"},
                },
            }
        ],
        "privacy": privacy or {"mode": "none"},
        "outputs": [{"name": "adapter", "format": "safetensors"}],
        "metrics": ["train_loss", "eval_loss", "perplexity"],
    }


@pytest.mark.parametrize("privacy", [None, {"mode": "dp", "epsilon": 8.0, "delta": 1e-3}])
def test_hf_sft_lora(tmp_path, tiny_llm_inputs, privacy):
    result = run_recipe(sft_recipe(privacy), tiny_llm_inputs, tmp_path)
    assert result["status"] == "succeeded", result.get("error")
    assert result["outputs"][0]["files"] == ["adapter_config.json", "adapter_model.safetensors"]
    assert result["metrics"]["perplexity"] >= 1.0
    if privacy:
        assert 7.0 < result["privacy_spent"]["epsilon"] <= 8.0
    from peft import PeftModel
    from transformers import AutoModelForCausalLM

    base = AutoModelForCausalLM.from_pretrained(tiny_llm_inputs / "base", local_files_only=True)
    PeftModel.from_pretrained(base, tmp_path / "outputs" / "adapter")


# ------------------------------------------------------------------------------------------ tier 3


def custom_recipe(script, metrics=("auc",), privacy=None) -> dict:
    return {
        "schema": "depa-training/recipe/v1",
        "name": "custom",
        "tier": 3,
        "family": "custom",
        "inputs": [{"alias": "left", "dataset_id": "tdp-a/left"}],
        "steps": [
            {
                "id": "run",
                "uses": "custom/entrypoint@1",
                "with": {
                    "command": [sys.executable, str(script)],
                    "outputs": [{"output": "model", "format": "safetensors"}],
                    "metrics": list(metrics),
                    "env": {"MODE": "test"},
                    "timeout_seconds": 60,
                },
            }
        ],
        "privacy": privacy or {"mode": "none"},
        "outputs": [{"name": "model", "format": "safetensors"}],
        "metrics": list(metrics),
    }


CUSTOM_SCRIPT = textwrap.dedent(
    """
    import json, os, struct
    from pathlib import Path
    rows = (Path(os.environ["DEPA_INPUTS_DIR"]) / "left" / "left.csv").read_text().splitlines()
    assert os.environ["MODE"] == "test" and "AWS_SECRET_ACCESS_KEY" not in os.environ
    header = json.dumps({"w": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}).encode()
    out = Path(os.environ["DEPA_OUTPUTS_DIR"]) / "model"
    (out / "model.safetensors").write_bytes(struct.pack("<Q", len(header)) + header + b"\\0\\0\\0\\0")
    report = {"metrics": {REPORTED: 0.5 + len(rows) / 1e6}}
    if os.environ.get("DP"):
        report["privacy_spent"] = {"epsilon": 0.9, "delta": 1e-6, "accountant": "rdp"}
    Path(os.environ["DEPA_REPORT"]).write_text(json.dumps(report))
    """
)


def test_custom_entrypoint(tmp_path, tabular_inputs, monkeypatch):
    script = tmp_path / "train.py"
    script.write_text(CUSTOM_SCRIPT.replace("REPORTED", '"auc"'))
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-leak")
    result = run_recipe(custom_recipe(script), tabular_inputs, tmp_path)
    assert result["status"] == "succeeded", result.get("error")
    assert result["metrics"]["auc"] > 0.5
    assert result["outputs"] == [{"name": "model", "format": "safetensors", "files": ["model.safetensors"]}]


def test_custom_entrypoint_undeclared_metric_fails(tmp_path, tabular_inputs):
    script = tmp_path / "train.py"
    script.write_text(CUSTOM_SCRIPT.replace("REPORTED", '"leak"'))
    result = run_recipe(custom_recipe(script), tabular_inputs, tmp_path)
    assert result["status"] == "failed" and "undeclared metric 'leak'" in result["error"]


def test_custom_entrypoint_dp_spend_checked(tmp_path, tabular_inputs):
    script = tmp_path / "train.py"
    script.write_text(CUSTOM_SCRIPT.replace("REPORTED", '"auc"').replace('os.environ.get("DP")', "True"))
    ok = run_recipe(
        custom_recipe(script, privacy={"mode": "dp", "epsilon": 1.0, "delta": 1e-5}),
        tabular_inputs,
        tmp_path / "ok",
    )
    assert ok["status"] == "succeeded" and ok["privacy_spent"]["accountant"] == "custom:rdp"
    over = run_recipe(
        custom_recipe(script, privacy={"mode": "dp", "epsilon": 0.5, "delta": 1e-5}),
        tabular_inputs,
        tmp_path / "over",
    )
    assert over["status"] == "failed" and "above its budget" in over["error"]
