"""ModelFactory: builds ``nn.Module`` instances from JSON configs (v1 format) using allowlists only.

Config: ``layers`` (name -> {class | submodule, params}), optional ``submodules`` (templates with
``$placeholders``), ``input`` names, ``forward`` steps ({ops, input, output}) and ``output`` names.
Ops are layer names, or allowlisted ``torch.*`` / ``F.*`` functions optionally with kwargs.
"""

from __future__ import annotations

from typing import Any

import torch
import torch.nn.functional as F
from torch import nn

from ..errors import SafetyError, StepError
from . import allowlists as al


def _substitute(value: Any, params: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        if value[1:] not in params:
            raise StepError(f"placeholder '{value}' has no value")
        return params[value[1:]]
    if isinstance(value, dict):
        return {k: _substitute(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, params) for v in value]
    return value


def _resolve_op(name: str):
    resolved = al.normalize_op(name)
    if resolved is None:
        raise SafetyError(f"operation '{name}' is not allowlisted")
    namespace, attr = resolved
    return getattr(F if namespace == "F" else torch, attr)


_SEQUENCE_OPS = {"cat", "stack", "concat"}


class ConfigModule(nn.Module):
    def __init__(
        self, config: dict[str, Any], submodules: dict[str, Any], params: dict[str, Any] | None = None
    ):
        super().__init__()
        self.layers = nn.ModuleDict()
        for name, entry in config.get("layers", {}).items():
            layer_params = _substitute(entry.get("params", {}), params or {})
            if "class" in entry:
                cls_name = al.normalize_layer_class(entry["class"])
                if cls_name is None:
                    raise SafetyError(f"layer class '{entry['class']}' is not allowlisted")
                try:
                    self.layers[name] = getattr(nn, cls_name)(**layer_params)
                except (TypeError, ValueError) as exc:
                    raise StepError(f"layer '{name}' ({cls_name}): {exc}") from None
            else:
                self.layers[name] = ConfigModule(submodules[entry["submodule"]], submodules, layer_params)
        self._inputs = [str(n) for n in config.get("input", [])]
        self._outputs = [str(n) for n in config.get("output", [])]
        self._steps = []
        for step in config.get("forward", []):
            ops = []
            for op in step["ops"]:
                if isinstance(op, str) and op in self.layers:
                    ops.append((op, None, {}))
                else:
                    path, kwargs = (op, {}) if isinstance(op, str) else (op[0], op[1] if len(op) > 1 else {})
                    ops.append((None, _resolve_op(path), dict(kwargs)))
            inputs = step.get("input", [])
            packed = len(inputs) == 1 and isinstance(inputs[0], list)
            self._steps.append((list(inputs[0]) if packed else list(inputs), ops, step.get("output")))

    def forward(self, *args: torch.Tensor) -> Any:
        if len(args) != len(self._inputs):
            raise StepError(f"model expects {len(self._inputs)} inputs, got {len(args)}")
        env: dict[str, Any] = dict(zip(self._inputs, args, strict=True))
        for inputs, ops, output in self._steps:
            current = [env[n] for n in inputs]
            for layer, fn, kwargs in ops:
                if layer is not None:
                    result = self.layers[layer](*current)
                elif fn.__name__ in _SEQUENCE_OPS:
                    result = fn(list(current), **kwargs)
                else:
                    result = fn(*current, **kwargs)
                current = [result]
            if isinstance(output, list):
                if len(output) == 1:
                    env[output[0]] = current[0]
                else:
                    env.update(zip(output, current[0], strict=True))
            elif output is not None:
                env[output] = current[0]
        if len(self._outputs) == 1:
            return env[self._outputs[0]]
        return tuple(env[n] for n in self._outputs)


def build_factory_model(config: dict[str, Any]) -> nn.Module:
    errors = al.check_factory_config(config)
    if errors:
        raise SafetyError(f"model config rejected: {errors[0]}")
    return ConfigModule(config, config.get("submodules", {}))


def build_arch_model(name: str, params: dict[str, Any]) -> nn.Module:
    if name not in al.ARCHITECTURES:
        raise SafetyError(f"architecture '{name}' is not allowlisted")
    if al.DENIED_ARCH_PARAMS & set(params):
        raise SafetyError("weight-loading parameters are not allowed")
    library, _, fn_name = name.partition(".")
    try:
        if library == "torchvision":
            import torchvision.models as tvm

            return getattr(tvm, fn_name)(weights=None, **params)
        if library == "timm":
            import timm

            return timm.create_model(fn_name, pretrained=False, **params)
        import monai.networks.nets as mnets

        return getattr(mnets, fn_name)(**params)
    except (TypeError, ValueError) as exc:
        raise StepError(f"invalid parameters for {name}: {exc}") from None


def count_parameters(build) -> int:
    """Count parameters of ``build()`` without allocating memory (meta device)."""
    with torch.device("meta"):
        model = build()
    return sum(p.numel() for p in model.parameters())
