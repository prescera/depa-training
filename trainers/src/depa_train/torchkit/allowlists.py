"""String allowlists for everything a torch recipe may name. Import-light (no torch import).

Static checks here run during validation; the runtime resolvers in ``model_factory``/``losses``
consult the same sets, so a name is either allowed in both places or in neither.
"""

from __future__ import annotations

import re
from typing import Any

NN_LAYERS = frozenset(
    """
    Linear Bilinear Conv1d Conv2d Conv3d ConvTranspose1d ConvTranspose2d ConvTranspose3d
    MaxPool1d MaxPool2d MaxPool3d AvgPool1d AvgPool2d AvgPool3d AdaptiveAvgPool1d AdaptiveAvgPool2d
    AdaptiveAvgPool3d AdaptiveMaxPool1d AdaptiveMaxPool2d BatchNorm1d BatchNorm2d BatchNorm3d GroupNorm
    LayerNorm RMSNorm InstanceNorm1d InstanceNorm2d InstanceNorm3d Dropout Dropout1d Dropout2d Dropout3d
    AlphaDropout ReLU ReLU6 LeakyReLU PReLU ELU SELU CELU GELU SiLU Mish Sigmoid Tanh Softplus Softmax
    LogSoftmax Hardswish Hardsigmoid Flatten Unflatten Identity Embedding EmbeddingBag LSTM GRU RNN
    MultiheadAttention TransformerEncoderLayer TransformerDecoderLayer Upsample PixelShuffle ZeroPad2d
    ConstantPad1d ConstantPad2d ReflectionPad2d ReplicationPad2d
    """.split()
)
TORCH_OPS = frozenset(
    """
    cat stack concat flatten reshape permute transpose unsqueeze squeeze chunk split gather index_select
    narrow sum mean std var amax amin argmax argmin norm exp log log1p sigmoid tanh softmax log_softmax relu
    matmul mm bmm addmm einsum roll flip rot90 abs clamp sqrt rsqrt pow mul add sub div neg where maximum
    minimum
    """.split()
)
FUNCTIONAL_OPS = frozenset(
    """
    relu relu6 leaky_relu elu selu celu gelu silu mish sigmoid tanh softmax log_softmax softplus hardswish
    hardsigmoid interpolate max_pool1d max_pool2d max_pool3d avg_pool1d avg_pool2d avg_pool3d
    adaptive_avg_pool1d adaptive_avg_pool2d adaptive_avg_pool3d adaptive_max_pool1d adaptive_max_pool2d
    dropout dropout1d dropout2d dropout3d pad normalize layer_norm group_norm
    """.split()
)
# Keyword arguments that could write into caller buffers or reach the filesystem.
DENIED_KWARGS = frozenset({"out", "file", "filename", "f", "path", "dir", "directory", "map_location"})

LOSS_CLASSES = frozenset(
    {
        f"torch.nn.{n}"
        for n in """
    BCELoss BCEWithLogitsLoss CrossEntropyLoss NLLLoss MSELoss L1Loss SmoothL1Loss HuberLoss KLDivLoss
    PoissonNLLLoss GaussianNLLLoss CosineEmbeddingLoss MarginRankingLoss HingeEmbeddingLoss
    MultiLabelSoftMarginLoss MultiMarginLoss SoftMarginLoss TripletMarginLoss
    """.split()
    }
    | {
        f"monai.losses.{n}"
        for n in "DiceLoss DiceCELoss DiceFocalLoss FocalLoss TverskyLoss GeneralizedDiceLoss".split()
    }
)
LOSS_FUNCTIONS = frozenset(
    {
        f"torch.nn.functional.{n}"
        for n in """
    binary_cross_entropy binary_cross_entropy_with_logits cross_entropy nll_loss mse_loss l1_loss
    smooth_l1_loss huber_loss kl_div
    """.split()
    }
    | {f"torch.{n}" for n in "exp log mean sum abs pow clamp sqrt".split()}
)
OPTIMIZERS = frozenset({"SGD", "Adam", "AdamW", "RMSprop", "Adagrad", "Adadelta", "Adamax", "NAdam", "RAdam"})
SCHEDULERS = frozenset(
    {
        "StepLR",
        "MultiStepLR",
        "ExponentialLR",
        "CosineAnnealingLR",
        "CyclicLR",
        "OneCycleLR",
        "LinearLR",
        "ConstantLR",
    }
)
ARCHITECTURES = frozenset(
    {
        f"torchvision.{n}"
        for n in """
    resnet18 resnet34 resnet50 mobilenet_v3_small mobilenet_v3_large efficientnet_b0 vgg11 densenet121
    squeezenet1_1 shufflenet_v2_x0_5 regnet_y_400mf convnext_tiny vit_b_16
    """.split()
    }
    | {
        f"timm.{n}"
        for n in """
    resnet18 resnet50 efficientnet_b0 mobilenetv3_small_100 convnext_atto vit_tiny_patch16_224
    deit_tiny_patch16_224 regnety_002
    """.split()
    }
    | {f"monai.{n}" for n in "UNet BasicUNet SegResNet AttentionUnet DenseNet121".split()}
)
# Constructor parameters that could fetch or load weights from outside the run.
DENIED_ARCH_PARAMS = frozenset(
    {
        "weights",
        "weights_backbone",
        "pretrained",
        "pretrained_cfg",
        "pretrained_cfg_overlay",
        "checkpoint_path",
        "cache_dir",
        "url",
        "file",
        "path",
        "progress",
        "model_dir",
    }
)

MAX_LAYERS = 1024
MAX_FORWARD_STEPS = 512
MAX_OPS_PER_STEP = 16
MAX_SUBMODULE_DEPTH = 8
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def normalize_layer_class(name: str) -> str | None:
    for prefix in ("nn.", "torch.nn."):
        if name.startswith(prefix) and name[len(prefix) :] in NN_LAYERS:
            return name[len(prefix) :]
    return None


def normalize_op(name: str) -> tuple[str, str] | None:
    """Map an op path to ('torch'|'F', attr) if allowlisted."""
    for prefix in ("F.", "torch.nn.functional."):
        if name.startswith(prefix) and name[len(prefix) :] in FUNCTIONAL_OPS:
            return ("F", name[len(prefix) :])
    if name.startswith("torch.") and name[len("torch.") :] in TORCH_OPS:
        return ("torch", name[len("torch.") :])
    return None


def normalize_loss(name: str) -> str | None:
    for short, full in (("nn.", "torch.nn."), ("F.", "torch.nn.functional.")):
        if name.startswith(short):
            name = full + name[len(short) :]
    return name if name in LOSS_CLASSES or name in LOSS_FUNCTIONS else None


def _is_json(value: Any, depth: int = 0) -> bool:
    if depth > 16:
        return False
    if value is None or isinstance(value, bool | int | float | str):
        return True
    if isinstance(value, list):
        return all(_is_json(v, depth + 1) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json(v, depth + 1) for k, v in value.items())
    return False


def check_factory_config(config: Any) -> list[str]:
    """Static validation of a ModelFactory config (layers / submodules / forward graph)."""
    if not isinstance(config, dict):
        return ["model config must be an object"]
    unknown = set(config) - {"submodules", "layers", "input", "forward", "output"}
    if unknown:
        return [f"unknown model config keys {sorted(unknown)}"]
    submodules = config.get("submodules", {})
    if not isinstance(submodules, dict):
        return ["'submodules' must be an object"]
    errors: list[str] = []
    for name, sub in submodules.items():
        if not _IDENT.match(str(name)):
            errors.append(f"submodule name '{name}' is not an identifier")
        errors.extend(f"submodules.{name}: {e}" for e in _check_graph(sub, submodules, placeholders=True))
    errors.extend(_check_graph(config, submodules, placeholders=False))
    errors.extend(_check_submodule_depth(submodules))
    return errors


def _check_graph(cfg: Any, submodules: dict, *, placeholders: bool) -> list[str]:
    if not isinstance(cfg, dict):
        return ["graph must be an object"]
    unknown = set(cfg) - {"layers", "input", "forward", "output", "submodules"}
    if unknown:
        return [f"unknown keys {sorted(unknown)}"]
    errors: list[str] = []
    layers = cfg.get("layers", {})
    inputs, forward, outputs = cfg.get("input", []), cfg.get("forward", []), cfg.get("output", [])
    if not isinstance(layers, dict) or len(layers) > MAX_LAYERS:
        return [f"'layers' must be an object with at most {MAX_LAYERS} entries"]
    if not (isinstance(inputs, list) and isinstance(forward, list) and isinstance(outputs, list)):
        return ["'input', 'forward' and 'output' must be lists"]
    if len(forward) > MAX_FORWARD_STEPS:
        errors.append(f"too many forward steps ({len(forward)} > {MAX_FORWARD_STEPS})")
    for lname, entry in layers.items():
        if not _IDENT.match(str(lname)):
            errors.append(f"layer name '{lname}' is not an identifier")
        if not isinstance(entry, dict):
            errors.append(f"layer '{lname}' must be an object")
            continue
        params = entry.get("params", {})
        if not isinstance(params, dict) or not _is_json(params):
            errors.append(f"layer '{lname}': params must be a JSON object")
        elif not placeholders and any(isinstance(v, str) and v.startswith("$") for v in params.values()):
            errors.append(f"layer '{lname}': placeholders are only allowed inside submodules")
        if set(entry) - {"class", "submodule", "params"}:
            errors.append(
                f"layer '{lname}': unknown keys {sorted(set(entry) - {'class', 'submodule', 'params'})}"
            )
        if ("class" in entry) == ("submodule" in entry):
            errors.append(f"layer '{lname}': needs exactly one of 'class' or 'submodule'")
        elif "class" in entry and normalize_layer_class(str(entry["class"])) is None:
            errors.append(f"layer '{lname}': class '{entry['class']}' is not allowlisted")
        elif "submodule" in entry and entry["submodule"] not in submodules:
            errors.append(f"layer '{lname}': unknown submodule '{entry['submodule']}'")
    defined = {str(n) for n in inputs}
    for i, step in enumerate(forward):
        where = f"forward[{i}]"
        if not isinstance(step, dict) or set(step) - {"ops", "input", "output"}:
            errors.append(f"{where}: must be an object with ops/input/output")
            continue
        ops, step_inputs, out = step.get("ops", []), step.get("input", []), step.get("output")
        if not isinstance(ops, list) or not ops or len(ops) > MAX_OPS_PER_STEP:
            errors.append(f"{where}: 'ops' must list 1..{MAX_OPS_PER_STEP} operations")
            ops = []
        flat = step_inputs[0] if len(step_inputs) == 1 and isinstance(step_inputs[0], list) else step_inputs
        for ref in flat if isinstance(flat, list) else []:
            if ref not in defined:
                errors.append(f"{where}: input '{ref}' is not defined before use")
        for op in ops:
            errors.extend(f"{where}: {e}" for e in _check_op(op, layers))
        for name in out if isinstance(out, list) else [out]:
            if name is not None:
                defined.add(str(name))
    for name in outputs:
        if name not in defined:
            errors.append(f"output '{name}' is never computed")
    return errors


def _check_op(op: Any, layers: dict) -> list[str]:
    if isinstance(op, str):
        if op in layers or normalize_op(op) is not None:
            return []
        return [f"operation '{op}' is not a layer name or an allowlisted op"]
    if isinstance(op, list) and len(op) in (1, 2) and isinstance(op[0], str):
        if normalize_op(op[0]) is None:
            return [f"operation '{op[0]}' is not allowlisted"]
        kwargs = op[1] if len(op) == 2 else {}
        if not isinstance(kwargs, dict) or not _is_json(kwargs):
            return [f"operation '{op[0]}': kwargs must be a JSON object"]
        denied = sorted(k for k in kwargs if k.lower() in DENIED_KWARGS)
        return [f"operation '{op[0]}': kwargs {denied} are not allowed"] if denied else []
    return [f"invalid operation spec {op!r}"]


def _check_submodule_depth(submodules: dict) -> list[str]:
    def depth(name: str, seen: tuple[str, ...]) -> int:
        if name in seen or len(seen) > MAX_SUBMODULE_DEPTH:
            return MAX_SUBMODULE_DEPTH + 1
        layers = submodules.get(name, {}).get("layers", {}) if isinstance(submodules.get(name), dict) else {}
        children = [e["submodule"] for e in layers.values() if isinstance(e, dict) and "submodule" in e]
        return 1 + max((depth(c, (*seen, name)) for c in children), default=0)

    return [
        f"submodule '{n}' is recursive or nested deeper than {MAX_SUBMODULE_DEPTH}"
        for n in submodules
        if depth(n, ()) > MAX_SUBMODULE_DEPTH
    ]
