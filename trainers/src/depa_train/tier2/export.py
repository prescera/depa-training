"""Producer side (run by the TDC on its own machine): ``nn.Module`` -> ``depa-graph/v1`` directory."""

from __future__ import annotations

import json
import operator
from pathlib import Path
from typing import Any

import torch
from torch import nn
from torch.export.graph_signature import InputKind, OutputKind

from . import GRAPH_FORMAT
from .ops import check_graph_document


def _encode(arg: Any) -> Any:
    if isinstance(arg, torch.fx.Node):
        return {"ref": arg.name}
    if isinstance(arg, list | tuple):
        return [_encode(a) for a in arg]
    if isinstance(arg, torch.dtype):
        return {"dtype": str(arg).removeprefix("torch.")}
    if isinstance(arg, torch.device):
        return {"device": arg.type}
    if isinstance(arg, torch.layout):
        return {"layout": str(arg).removeprefix("torch.")}
    if isinstance(arg, torch.memory_format):
        return {"memory_format": str(arg).removeprefix("torch.")}
    if arg is None or isinstance(arg, bool | int | float | str):
        return arg
    raise TypeError(f"cannot encode argument of type {type(arg).__name__}")


def export_graph(
    model: nn.Module,
    example_inputs: tuple[torch.Tensor, ...],
    out_dir: str | Path,
    *,
    dynamic_batch: bool = True,
) -> list[str]:
    """Export ``model`` to ``out_dir`` (graph.json + weights.safetensors). Returns validation errors.

    The model is traced with ``torch.export`` in its current train/eval mode and decomposed to core
    ATen. A non-empty return value means the graph uses ops outside the tier-2 allowlist.
    """
    from safetensors.torch import save_file

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    dynamic = None
    if dynamic_batch:
        batch = torch.export.Dim("batch", min=1)
        dynamic = tuple({0: batch} for _ in example_inputs)
    program = torch.export.export(model, example_inputs, dynamic_shapes=dynamic).run_decompositions()
    sig = program.graph_signature
    specs = {s.arg.name: s for s in sig.input_specs}
    state = {**program.state_dict, **program.constants}
    doc: dict[str, Any] = {"format": GRAPH_FORMAT, "inputs": [], "params": {}, "buffers": {}, "nodes": []}
    weights: dict[str, torch.Tensor] = {}
    for node in program.graph.nodes:
        if node.op == "placeholder":
            spec = specs[node.name]
            if spec.kind == InputKind.USER_INPUT:
                doc["inputs"].append(node.name)
            elif spec.kind == InputKind.PARAMETER:
                doc["params"][node.name] = {
                    "target": spec.target,
                    "trainable": bool(state[spec.target].requires_grad),
                }
                weights[spec.target] = state[spec.target]
            elif spec.kind in (InputKind.BUFFER, InputKind.CONSTANT_TENSOR):
                doc["buffers"][node.name] = {"target": spec.target}
                weights[spec.target] = state[spec.target]
            else:
                raise ValueError(f"unsupported graph input kind {spec.kind}")
        elif node.op == "call_function":
            target = "getitem" if node.target is operator.getitem else node.target.name()
            doc["nodes"].append(
                {
                    "name": node.name,
                    "op": target,
                    "args": _encode(node.args),
                    "kwargs": {k: _encode(v) for k, v in node.kwargs.items()},
                }
            )
        elif node.op == "output":
            doc["outputs"] = _encode(list(node.args[0]))
        else:
            raise ValueError(f"unsupported node kind {node.op}")
    doc["buffer_mutations"] = {
        s.arg.name: s.target for s in sig.output_specs if s.kind == OutputKind.BUFFER_MUTATION
    }
    save_file(
        {k: v.detach().contiguous().clone() for k, v in weights.items()}, str(out / "weights.safetensors")
    )
    (out / "graph.json").write_text(json.dumps(doc, indent=1))
    return check_graph_document(doc)
