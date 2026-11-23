"""Rebuild a validated ``depa-graph/v1`` artifact into a trainable ``nn.Module`` (no pickle, no eval)."""

from __future__ import annotations

import json
import operator
from pathlib import Path
from typing import Any

import torch
from torch import nn

from ..errors import SafetyError
from .ops import GETITEM, check_graph_document, check_op

MAX_GRAPH_BYTES = 32 << 20


def _resolve(target: str):
    error = check_op(target)
    if error:
        raise SafetyError(error)
    if target == GETITEM:
        return operator.getitem
    name, _, overload = target.removeprefix("aten::").partition(".")
    packet = getattr(torch.ops.aten, name)
    return getattr(packet, overload or "default")


class GraphModel(nn.Module):
    """Wraps the rebuilt fx graph; applies declared buffer mutations after each forward."""

    def __init__(self, gm: torch.fx.GraphModule, n_user_outputs: int, mutations: list[tuple[int, str]]):
        super().__init__()
        self.gm = gm
        self._n_user = n_user_outputs
        self._mutations = mutations

    def forward(self, *args: torch.Tensor) -> Any:
        outs = self.gm(*args)
        outs = outs if isinstance(outs, tuple) else (outs,)
        if self._mutations:
            with torch.no_grad():
                for index, attr in self._mutations:
                    getattr(self.gm, attr).copy_(outs[index])
        user = outs[len(self._mutations) :]
        return user[0] if self._n_user == 1 else tuple(user)

    @property
    def has_buffer_mutations(self) -> bool:
        return bool(self._mutations)


def _attr(target: str) -> str:
    return target.replace(".", "__")


def load_graph_document(directory: Path) -> dict[str, Any]:
    path = directory / "graph.json"
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_GRAPH_BYTES:
        raise SafetyError("graph.json is missing or too large")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SafetyError("graph.json is not valid JSON") from None
    errors = check_graph_document(doc)
    if errors:
        raise SafetyError(
            f"graph rejected: {errors[0]}" + (f" (+{len(errors) - 1} more)" if len(errors) > 1 else "")
        )
    return doc


def build_graph_model(directory: Path, max_parameters: int) -> tuple[GraphModel, dict[str, Any]]:
    from safetensors.torch import load_file

    doc = load_graph_document(directory)
    weights_path = directory / "weights.safetensors"
    if not weights_path.is_file() or weights_path.is_symlink():
        raise SafetyError("weights.safetensors is missing")
    weights = load_file(str(weights_path))
    root = nn.Module()
    graph = torch.fx.Graph()
    env: dict[str, torch.fx.Node] = {}
    n_params = 0
    for name in doc["inputs"]:
        env[name] = graph.placeholder(name)
    for kind in ("params", "buffers"):
        for name, spec in doc.get(kind, {}).items():
            tensor = weights.get(spec["target"])
            if tensor is None:
                raise SafetyError(f"weights.safetensors lacks '{spec['target']}'")
            attr = _attr(spec["target"])
            if kind == "params":
                n_params += tensor.numel()
                root.register_parameter(
                    attr, nn.Parameter(tensor, requires_grad=bool(spec.get("trainable", True)))
                )
            else:
                root.register_buffer(attr, tensor)
            env[name] = graph.get_attr(attr)
    if n_params > max_parameters:
        raise SafetyError(f"graph has {n_params} parameters, above max_parameters={max_parameters}")

    def decode(arg: Any) -> Any:
        if isinstance(arg, list):
            return [decode(a) for a in arg]
        if isinstance(arg, dict):
            ((key, value),) = arg.items()
            if key == "ref":
                return env[value]
            if key == "dtype":
                return getattr(torch, value)
            if key == "device":
                return torch.device("cpu")
            if key == "layout":
                return torch.strided
            return getattr(torch, value)  # memory_format, validated against MEMORY_FORMATS
        return arg

    for node in doc["nodes"]:
        fn = _resolve(node["op"])
        args = tuple(decode(node.get("args", [])))
        kwargs = {k: decode(v) for k, v in node.get("kwargs", {}).items()}
        env[node["name"]] = graph.call_function(fn, args, kwargs)
    outputs = decode(doc["outputs"])
    mutations_doc = doc.get("buffer_mutations", {})
    out_names = [o["ref"] for o in doc["outputs"] if isinstance(o, dict) and "ref" in o]
    mutated = [(i, _attr(mutations_doc[n])) for i, n in enumerate(out_names) if n in mutations_doc]
    if [i for i, _ in mutated] != list(range(len(mutated))):
        raise SafetyError("buffer mutation outputs must precede user outputs")
    graph.output(outputs[0] if len(outputs) == 1 else tuple(outputs))
    gm = torch.fx.GraphModule(root, graph)
    gm.graph.lint()
    return GraphModel(gm, len(outputs) - len(mutated), mutated), doc


def trained_weights(model: GraphModel, doc: dict[str, Any]) -> dict[str, torch.Tensor]:
    """Weights keyed by their original module paths, for writing back as ``weights.safetensors``."""
    out = {}
    for kind in ("params", "buffers"):
        for spec in doc.get(kind, {}).values():
            out[spec["target"]] = getattr(model.gm, _attr(spec["target"])).detach().contiguous().clone()
    return out
