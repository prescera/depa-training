"""Allowlist walker for Keras 3 model configs (import-light; used before and after deserialization).

Every object reference in a Keras config is a dict with ``module`` / ``class_name``. Deserializing
an arbitrary ``module`` would import arbitrary code, and ``Lambda`` layers carry Python bytecode, so
every reference must be a built-in Keras class on this list. Deserialization additionally runs with
``safe_mode=True``.
"""

from __future__ import annotations

from typing import Any

KERAS_MODULES = frozenset(
    {
        "keras",
        "keras.layers",
        "keras.models",
        "keras.initializers",
        "keras.regularizers",
        "keras.constraints",
        "keras.src.models.functional",
        "keras.src.models.sequential",
    }
)
KERAS_CLASSES = frozenset(
    """
    Sequential Functional InputLayer __keras_tensor__ DTypePolicy
    Dense EinsumDense Conv1D Conv2D Conv3D Conv1DTranspose Conv2DTranspose SeparableConv2D DepthwiseConv2D
    MaxPooling1D MaxPooling2D MaxPooling3D AveragePooling1D AveragePooling2D AveragePooling3D
    GlobalAveragePooling1D GlobalAveragePooling2D GlobalMaxPooling1D GlobalMaxPooling2D Flatten Reshape
    Permute RepeatVector Dropout SpatialDropout1D SpatialDropout2D GaussianNoise BatchNormalization
    LayerNormalization GroupNormalization Activation ReLU LeakyReLU PReLU ELU Softmax Embedding LSTM GRU
    SimpleRNN Bidirectional Concatenate Add Subtract Multiply Average Maximum Minimum Rescaling
    ZeroPadding1D ZeroPadding2D Cropping2D UpSampling1D UpSampling2D MultiHeadAttention
    GlorotUniform GlorotNormal HeUniform HeNormal LecunUniform LecunNormal Zeros Ones Constant RandomNormal
    RandomUniform TruncatedNormal Orthogonal Identity VarianceScaling
    L1 L2 L1L2 OrthogonalRegularizer MaxNorm MinMaxNorm NonNeg UnitNorm
    """.split()
)
ACTIVATIONS = frozenset(
    """
    linear relu relu6 leaky_relu elu selu gelu silu swish mish sigmoid hard_sigmoid tanh softmax
    log_softmax softplus softsign exponential
    """.split()
)
COMPILE_LOSSES = frozenset(
    {
        "binary_crossentropy",
        "categorical_crossentropy",
        "sparse_categorical_crossentropy",
        "mean_squared_error",
        "mean_absolute_error",
        "huber",
        "kl_divergence",
        "poisson",
    }
)
OPTIMIZERS = frozenset({"sgd", "adam", "adamw", "rmsprop", "adagrad", "adadelta", "adamax", "nadam"})
_DENIED_KEYS = frozenset({"function", "function_type", "output_shape_type", "module_name"})
MAX_DEPTH = 64


def check_keras_config(config: Any) -> list[str]:
    errors: list[str] = []

    def walk(node: Any, path: str, depth: int) -> None:
        if depth > MAX_DEPTH:
            errors.append(f"{path}: config nested too deeply")
            return
        if isinstance(node, dict):
            if "class_name" in node:
                cls, module = node.get("class_name"), node.get("module")
                if cls not in KERAS_CLASSES:
                    errors.append(f"{path}: class '{cls}' is not allowlisted")
                if module is not None and module not in KERAS_MODULES:
                    errors.append(f"{path}: module '{module}' is not allowlisted")
                if node.get("registered_name") not in (None, cls):
                    errors.append(f"{path}: custom registered objects are not allowed")
            for key, value in node.items():
                if key in _DENIED_KEYS:
                    errors.append(f"{path}.{key}: not allowed (serialized Python functions)")
                elif key == "activation" and isinstance(value, str) and value not in ACTIVATIONS:
                    errors.append(f"{path}.activation: '{value}' is not allowlisted")
                elif key == "activation" and isinstance(value, dict):
                    errors.append(f"{path}.activation: only built-in activation names are allowed")
                else:
                    walk(value, f"{path}.{key}", depth + 1)
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]", depth + 1)

    if not isinstance(config, dict) or config.get("class_name") not in ("Sequential", "Functional"):
        return ["model config must be a Keras Sequential or Functional model"]
    walk(config, "model", 0)
    return errors
