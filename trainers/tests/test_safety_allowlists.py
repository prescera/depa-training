"""Class/function allowlists for anything named in configuration (runtime resolvers)."""

from __future__ import annotations

import pytest

from depa_train.errors import SafetyError
from depa_train.safety.keras_config import check_keras_config
from depa_train.torchkit.allowlists import check_factory_config

torch = pytest.importorskip("torch")


def factory(layers=None, forward=None):
    return {
        "layers": layers or {"fc": {"class": "nn.Linear", "params": {"in_features": 2, "out_features": 1}}},
        "input": ["x"],
        "forward": forward or [{"ops": ["fc"], "input": ["x"], "output": "y"}],
        "output": ["y"],
    }


@pytest.mark.parametrize(
    ("config", "needle"),
    [
        (factory(layers={"a": {"class": "os.system"}}), "class 'os.system' is not allowlisted"),
        (factory(layers={"a": {"class": "nn.Module"}}), "class 'nn.Module' is not allowlisted"),
        (factory(layers={"a": {"class": "builtins.eval"}}), "is not allowlisted"),
        (
            factory(forward=[{"ops": ["torch.load"], "input": ["x"], "output": "y"}]),
            "'torch.load' is not a layer",
        ),
        (
            factory(forward=[{"ops": [["torch.save", {}]], "input": ["x"], "output": "y"}]),
            "'torch.save' is not allowlisted",
        ),
        (
            factory(forward=[{"ops": [["torch.cat", {"out": "x"}]], "input": ["x"], "output": "y"}]),
            "kwargs ['out']",
        ),
        (
            factory(forward=[{"ops": ["fc"], "input": ["ghost"], "output": "y"}]),
            "input 'ghost' is not defined",
        ),
        (
            {
                **factory(),
                "submodules": {
                    "S": {"layers": {"s": {"submodule": "S"}}, "input": [], "forward": [], "output": []}
                },
            },
            "recursive",
        ),
    ],
)
def test_factory_static_rejections(config, needle):
    assert any(needle in e for e in check_factory_config(config)), check_factory_config(config)


def test_factory_runtime_rejects_even_if_static_check_skipped():
    from depa_train.torchkit.model_factory import ConfigModule, build_factory_model

    with pytest.raises(SafetyError):
        build_factory_model(factory(layers={"a": {"class": "os.system"}}))
    bad = factory(layers={"a": {"class": "subprocess.Popen"}})
    with pytest.raises(SafetyError):
        ConfigModule(bad, {})


def test_factory_builds_and_runs():
    from depa_train.torchkit.model_factory import build_factory_model

    model = build_factory_model(
        factory(forward=[{"ops": ["fc", "torch.sigmoid"], "input": ["x"], "output": "y"}])
    )
    out = model(torch.zeros(3, 2))
    assert out.shape == (3, 1)


def test_loss_allowlist_and_expression_sandbox():
    from depa_train.torchkit.config import LossSpec
    from depa_train.torchkit.losses import build_loss

    with pytest.raises(SafetyError, match="not allowlisted"):
        build_loss(LossSpec.model_validate({"class": "torch.load"}))
    for expr in ("__import__('os').system('true')", "mse.real", "mse[0]", "[mse]", "(lambda: 1)()"):
        spec = LossSpec.model_validate({"expression": expr, "components": {"mse": {"class": "nn.MSELoss"}}})
        with pytest.raises(SafetyError, match="not allowed in loss expressions"):
            build_loss(spec)
    spec = LossSpec.model_validate(
        {
            "expression": "0.5 * mse + 2 * l1",
            "components": {"mse": {"class": "nn.MSELoss"}, "l1": {"class": "nn.L1Loss"}},
        }
    )
    value = build_loss(spec)(torch.ones(2, 1), torch.zeros(2, 1))
    assert float(value) == pytest.approx(2.5)


def test_arch_allowlist_runtime():
    from depa_train.torchkit.model_factory import build_arch_model

    with pytest.raises(SafetyError):
        build_arch_model("torchvision.resnet18", {"weights": "IMAGENET1K_V1"})
    with pytest.raises(SafetyError):
        build_arch_model("torch.hub.load", {})


def test_optimizer_scheduler_allowlist():
    from depa_train.torchkit.config import OptimizerSpec, SchedulerSpec
    from depa_train.torchkit.train import build_optimizer, build_scheduler

    params = [torch.nn.Parameter(torch.zeros(1))]
    with pytest.raises(SafetyError):
        build_optimizer(OptimizerSpec(name="LBFGS"), params)
    opt = build_optimizer(OptimizerSpec(name="SGD", params={"lr": 0.1}), params)
    with pytest.raises(SafetyError):
        build_scheduler(SchedulerSpec(name="LambdaLR"), opt)


def test_keras_config_walker():
    ok = {
        "module": "keras",
        "class_name": "Sequential",
        "config": {
            "layers": [
                {
                    "module": "keras.layers",
                    "class_name": "Dense",
                    "config": {"units": 1, "activation": "sigmoid"},
                    "registered_name": None,
                }
            ]
        },
    }
    assert check_keras_config(ok) == []
    bad = {
        "module": "keras",
        "class_name": "Sequential",
        "config": {
            "layers": [
                {
                    "module": "keras.layers",
                    "class_name": "Dense",
                    "config": {"activation": "evil_fn"},
                    "registered_name": "custom>Dense",
                }
            ]
        },
    }
    errors = "\n".join(check_keras_config(bad))
    assert "'evil_fn' is not allowlisted" in errors and "custom registered objects" in errors
