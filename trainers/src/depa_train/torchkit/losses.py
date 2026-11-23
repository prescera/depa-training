"""Loss composer (v1 format): allowlisted atomic losses combined by a safe arithmetic expression."""

from __future__ import annotations

import ast
import importlib
from collections.abc import Callable
from typing import Any

import torch

from ..errors import SafetyError, StepError
from . import allowlists as al
from .config import LossSpec

LossFn = Callable[[torch.Tensor, torch.Tensor, dict[str, Any]], torch.Tensor]


def _resolve(path: str) -> Any:
    full = al.normalize_loss(path)
    if full is None:
        raise SafetyError(f"loss '{path}' is not allowlisted")
    module_name, _, attr = full.rpartition(".")
    return getattr(importlib.import_module(module_name), attr)


def _param(value: Any, outputs: torch.Tensor, targets: torch.Tensor, cache: dict[str, Any]) -> Any:
    if value in ("output", "outputs"):
        return outputs
    if value in ("target", "targets"):
        return targets
    if isinstance(value, str) and value.lstrip("-").startswith("$"):
        negate, ref = value.startswith("-"), value.lstrip("-")[1:]
        if ref not in cache:
            raise StepError(f"loss references unknown component '{ref}'")
        return -cache[ref] if negate else cache[ref]
    if isinstance(value, list):
        return [_param(v, outputs, targets, cache) for v in value]
    if isinstance(value, dict):
        return {k: _param(v, outputs, targets, cache) for k, v in value.items()}
    return value


def _atomic(spec: LossSpec) -> LossFn:
    obj = _resolve(spec.class_ or "")
    if isinstance(obj, type):
        try:
            instance = obj(**spec.params)
        except (TypeError, ValueError) as exc:
            raise StepError(f"invalid loss parameters for {spec.class_}: {exc}") from None
        return lambda outputs, targets, cache: instance(outputs, targets)
    return lambda outputs, targets, cache: obj(**_param(spec.params, outputs, targets, cache))


_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Pow: lambda a, b: a**b,
}


def _compile_expression(expression: str) -> Callable[[dict[str, Any]], Any]:
    """Arithmetic over names and numbers only: no calls, attributes, subscripts or comprehensions."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        raise StepError("loss expression is not valid") from None

    def ev(node: ast.AST, names: dict[str, Any]) -> Any:
        if isinstance(node, ast.Expression):
            return ev(node.body, names)
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, int | float)
            and not isinstance(node.value, bool)
        ):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise StepError(f"unknown name '{node.id}' in loss expression")
            return names[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd | ast.USub):
            value = ev(node.operand, names)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            return _BINOPS[type(node.op)](ev(node.left, names), ev(node.right, names))
        raise SafetyError(f"construct '{type(node).__name__}' is not allowed in loss expressions")

    ev(tree, _ValidateNames())  # reject disallowed constructs before training starts
    return lambda names: ev(tree, names)


class _ValidateNames(dict):
    """Name mapping used for a dry structural pass: every name evaluates to 1.0."""

    def __contains__(self, key: object) -> bool:
        return True

    def __getitem__(self, key: str) -> float:
        return 1.0


def build_loss(spec: LossSpec) -> Callable[[torch.Tensor, torch.Tensor], torch.Tensor]:
    fn = _build(spec)

    def loss(outputs: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
        return fn(outputs, targets, {})

    return loss


def _build(spec: LossSpec) -> LossFn:
    if spec.class_ is not None:
        return _atomic(spec)
    components = {name: _build(comp) for name, comp in spec.components.items()}
    evaluate = _compile_expression(spec.expression or "")

    def composed(outputs: torch.Tensor, targets: torch.Tensor, cache: dict[str, Any]) -> torch.Tensor:
        for name, comp in components.items():
            if name not in cache:
                cache[name] = comp(outputs, targets, cache)
        value = evaluate({**spec.variables, **cache})
        if isinstance(value, torch.Tensor) and value.ndim > 0 and spec.reduction != "none":
            value = value.mean() if spec.reduction == "mean" else value.sum()
        return value

    return composed
