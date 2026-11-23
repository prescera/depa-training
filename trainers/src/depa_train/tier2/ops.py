"""ATen op allowlist and structural validation of ``graph.json`` (import-light, no torch)."""

from __future__ import annotations

import re
from typing import Any

from . import GRAPH_FORMAT

# Core ATen operators (overload-agnostic). Functional only: no in-place variants, no ``out=``
# overloads, no uninitialised allocation (``empty*``), no custom namespaces, no higher-order ops.
ATEN_OPS = frozenset(
    """
    convolution addmm mm bmm matmul linear add sub rsub mul div neg relu gelu silu sigmoid tanh exp log
    log1p sqrt rsqrt pow abs clamp clamp_min clamp_max maximum minimum where view _unsafe_view reshape
    permute transpose t expand unsqueeze squeeze cat stack split split_with_sizes slice select
    index_select gather flatten clone alias _to_copy native_dropout dropout native_group_norm
    native_layer_norm _native_batch_norm_legit_functional _native_batch_norm_legit_no_training
    native_batch_norm max_pool2d_with_indices max_pool1d avg_pool2d avg_pool1d adaptive_avg_pool2d
    _adaptive_avg_pool2d mean sum amax amin argmax argmin _softmax _log_softmax softmax log_softmax
    upsample_bilinear2d upsample_nearest2d constant_pad_nd embedding full full_like zeros zeros_like ones
    ones_like arange scalar_tensor sym_size sym_numel sym_stride eq ne lt gt le ge logical_not
    logical_and logical_or leaky_relu hardtanh elu hardswish hardsigmoid var std detach lift_fresh_copy
    expand_copy permute_copy view_copy squeeze_copy unsqueeze_copy slice_copy select_copy
    alias_copy repeat erf sign floor ceil round reciprocal bitwise_not
    """.split()
)
GETITEM = "getitem"
_OP = re.compile(r"^aten::([a-z_][a-z0-9_]*)(?:\.([A-Za-z0-9_]+))?$")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_TARGET = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,255}$")
DTYPES = frozenset(
    {"float32", "float64", "float16", "bfloat16", "int64", "int32", "int16", "int8", "uint8", "bool"}
)
LAYOUTS = frozenset({"strided"})
MEMORY_FORMATS = frozenset({"contiguous_format", "preserve_format", "channels_last"})
MAX_NODES = 100_000
MAX_STRING = 64


def check_op(target: str) -> str | None:
    """Return an error for a disallowed op target, else None."""
    if target == GETITEM:
        return None
    m = _OP.match(target)
    if m is None:
        return f"op '{target}' is not an ATen operator (custom namespaces are not allowed)"
    name, overload = m.group(1), m.group(2) or "default"
    if name not in ATEN_OPS:
        return f"op 'aten::{name}' is not on the tier-2 allowlist"
    if "out" in overload.lower():
        return f"op 'aten::{name}.{overload}' writes into caller buffers ('out' overload)"
    return None


def _check_arg(arg: Any, defined: set[str], errors: list[str], where: str, depth: int = 0) -> None:
    if depth > 8:
        errors.append(f"{where}: arguments nested too deeply")
    elif arg is None or isinstance(arg, bool | int | float):
        return
    elif isinstance(arg, str):
        if len(arg) > MAX_STRING:
            errors.append(f"{where}: string argument too long")
    elif isinstance(arg, list):
        for item in arg:
            _check_arg(item, defined, errors, where, depth + 1)
    elif isinstance(arg, dict) and len(arg) == 1:
        ((key, value),) = arg.items()
        if key == "ref":
            if value not in defined:
                errors.append(f"{where}: reference to undefined value '{value}'")
        elif key == "dtype":
            if value not in DTYPES:
                errors.append(f"{where}: dtype '{value}' not allowed")
        elif key == "device":
            if value != "cpu":
                errors.append(f"{where}: only the 'cpu' device is allowed")
        elif key == "layout":
            if value not in LAYOUTS:
                errors.append(f"{where}: layout '{value}' not allowed")
        elif key == "memory_format":
            if value not in MEMORY_FORMATS:
                errors.append(f"{where}: memory format '{value}' not allowed")
        else:
            errors.append(f"{where}: unknown argument encoding '{key}'")
    else:
        errors.append(f"{where}: unsupported argument")


def check_graph_document(doc: Any) -> list[str]:
    """Structural validation of a parsed ``graph.json``. Returns errors (empty = valid)."""
    if not isinstance(doc, dict) or doc.get("format") != GRAPH_FORMAT:
        return [f"graph.json must be a '{GRAPH_FORMAT}' document"]
    allowed = {"format", "inputs", "params", "buffers", "nodes", "outputs", "buffer_mutations"}
    if set(doc) - allowed:
        return [f"graph.json has unknown keys {sorted(set(doc) - allowed)}"]
    errors: list[str] = []
    inputs, params, buffers = doc.get("inputs"), doc.get("params", {}), doc.get("buffers", {})
    nodes, outputs, mutations = doc.get("nodes"), doc.get("outputs"), doc.get("buffer_mutations", {})
    if not (
        isinstance(inputs, list) and inputs and all(isinstance(i, str) and _NAME.match(i) for i in inputs)
    ):
        return ["graph.json 'inputs' must be a non-empty list of names"]
    if not isinstance(params, dict) or not isinstance(buffers, dict) or not isinstance(mutations, dict):
        return ["graph.json 'params', 'buffers' and 'buffer_mutations' must be objects"]
    if not isinstance(nodes, list) or len(nodes) > MAX_NODES:
        return [f"graph.json 'nodes' must be a list of at most {MAX_NODES} nodes"]
    if not isinstance(outputs, list) or not outputs:
        return ["graph.json 'outputs' must be a non-empty list"]
    defined = set(inputs)
    for kind, table in (("params", params), ("buffers", buffers)):
        for name, spec in table.items():
            if not _NAME.match(name) or name in defined:
                errors.append(f"{kind}: invalid or duplicate name '{name}'")
            if (
                not isinstance(spec, dict)
                or not isinstance(spec.get("target"), str)
                or not _TARGET.match(spec["target"])
            ):
                errors.append(f"{kind}.{name}: needs a 'target' module path")
            elif set(spec) - {"target", "trainable"}:
                errors.append(f"{kind}.{name}: unknown keys")
            defined.add(name)
    for i, node in enumerate(nodes):
        where = f"nodes[{i}]"
        if not isinstance(node, dict) or set(node) - {"name", "op", "args", "kwargs"}:
            errors.append(f"{where}: must be an object with name/op/args/kwargs")
            continue
        name, op = node.get("name"), node.get("op")
        if not isinstance(name, str) or not _NAME.match(name) or name in defined:
            errors.append(f"{where}: invalid or duplicate node name")
            continue
        if not isinstance(op, str):
            errors.append(f"{where}: missing op")
            continue
        op_error = check_op(op)
        if op_error:
            errors.append(f"{where}: {op_error}")
        args, kwargs = node.get("args", []), node.get("kwargs", {})
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            errors.append(f"{where}: args must be a list and kwargs an object")
            continue
        _check_arg(args, defined, errors, where)
        for key, value in kwargs.items():
            if not _NAME.match(str(key)) or key == "out":
                errors.append(f"{where}: kwarg '{key}' not allowed")
            _check_arg(value, defined, errors, where)
        defined.add(name)
    _check_arg(outputs, defined, errors, "outputs")
    buffer_targets = {spec.get("target") for spec in buffers.values() if isinstance(spec, dict)}
    for node_name, target in mutations.items():
        if node_name not in defined or target not in buffer_targets:
            errors.append(f"buffer_mutations: invalid entry '{node_name}'")
    return errors
