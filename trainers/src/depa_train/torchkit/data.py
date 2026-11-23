"""Turn ``ArrayData`` sources into per-split NumPy arrays (features, targets) for torch and Keras."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from ..artifacts import TabularSplits
from ..data import ArrayData, ImageFolder, ImagePairs, TabularRef, TensorFile
from ..data.readers import read_tensor_file, split_indices
from ..errors import StepError
from ..safety.files import resolve_files
from ..types import StepRef

if TYPE_CHECKING:
    from ..context import StepContext

Arrays = dict[str, tuple[np.ndarray, np.ndarray]]
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg"})


def _fractions(splits) -> dict[str, float]:
    return {"train": splits.train, "val": splits.val, "test": splits.test}


def _shape_targets(y: np.ndarray, task: str) -> np.ndarray:
    if task == "multiclass":
        if y.dtype.kind == "f" and not np.all(np.mod(y, 1) == 0):
            raise StepError("multiclass targets must be integer class indices")
        return y.reshape(-1).astype(np.int64)
    if task in ("binary", "regression"):
        return y.reshape(len(y), -1).astype(np.float32)
    return y.astype(np.float32)


def _load_image(path: Path, grayscale: bool, size: tuple[int, int] | None = None) -> np.ndarray:
    from PIL import Image

    with Image.open(path) as source:
        img = source.convert("L" if grayscale else "RGB")
    if size is not None:
        img = img.resize((size[1], size[0]))
    arr = np.asarray(img, dtype=np.float32) / 255.0
    return arr[None] if arr.ndim == 2 else arr.transpose(2, 0, 1)


def _load_nifti_slice(path: Path) -> np.ndarray:
    import nibabel as nib

    volume = np.asarray(nib.load(str(path)).dataobj, dtype=np.float32)
    if volume.ndim == 3:
        volume = volume[:, :, volume.shape[2] // 2]
    if volume.ndim != 2:
        raise StepError("NIfTI inputs must be 2-D images or 3-D volumes")
    lo, hi = float(volume.min()), float(volume.max())
    return ((volume - lo) / (hi - lo) if hi > lo else np.zeros_like(volume))[None]


def _image_pairs(ctx: StepContext, spec: ImagePairs) -> tuple[np.ndarray, np.ndarray]:
    suffixes = frozenset({".png"}) if spec.format == "png" else frozenset({".nii", ".nii.gz"})
    images, masks = [], []
    incomplete = 0
    for alias in spec.inputs:
        root = ctx.input_root(alias)
        sample_dirs = sorted(p for p in root.glob(spec.samples) if p.is_dir() and not p.is_symlink())
        for sample in sample_dirs:
            rel = sample.relative_to(root).as_posix()
            img_files = resolve_files(root, f"{rel}/{spec.image}", suffixes=suffixes)
            mask_files = resolve_files(root, f"{rel}/{spec.mask}", suffixes=suffixes)
            if len(img_files) > 1 or len(mask_files) > 1:
                raise StepError("a sample directory matches several images or masks")
            if not img_files or not mask_files:
                incomplete += 1
                continue
            load = (lambda p: _load_image(p, True)) if spec.format == "png" else _load_nifti_slice
            image, mask = load(img_files[0]), load(mask_files[0])
            mask = (mask > spec.mask_threshold).astype(np.float32)
            if spec.skip_empty_masks and not mask.any():
                continue
            images.append(image)
            masks.append(mask)
            if spec.max_samples is not None and len(images) >= spec.max_samples:
                break
    ctx.log("image_pairs", samples=len(images), incomplete_skipped=incomplete)
    if not images:
        raise StepError("no image/mask pairs found")
    if len({a.shape for a in images}) != 1 or len({m.shape for m in masks}) != 1:
        raise StepError("all images (and masks) must have the same shape")
    return np.stack(images), np.stack(masks)


def _image_folder(ctx: StepContext, spec: ImageFolder) -> tuple[np.ndarray, np.ndarray]:
    images, labels = [], []
    for alias in spec.inputs:
        for label, cls in enumerate(spec.classes):
            if "/" in cls or cls in (".", ".."):
                raise StepError("class names must be plain directory names")
            if not (ctx.input_root(alias) / cls).is_dir():
                continue
            for path in ctx.files(alias, f"{cls}/*", suffixes=_IMAGE_SUFFIXES):
                images.append(_load_image(path, spec.grayscale, spec.size))
                labels.append(label)
    if not images:
        raise StepError("no images found in the class directories")
    X, y = np.stack(images), np.asarray(labels, dtype=np.int64)
    if spec.max_samples is not None:
        X, y = X[: spec.max_samples], y[: spec.max_samples]
    return X, y


def load_arrays(ctx: StepContext, data: ArrayData, task: str) -> Arrays:
    """Return {split: (X float32, y)} with targets shaped for ``task``."""
    if isinstance(data, TabularRef):
        splits = ctx.artifact(StepRef(step=data.step))
        if not isinstance(splits, TabularSplits):
            raise StepError(f"step '{data.step}' did not produce tabular splits")
        if (task == "regression") != (splits.task == "regression"):
            raise StepError(f"task '{task}' does not match the tabular split task '{splits.task}'")
        return {name: (s.X, _shape_targets(s.y, task)) for name, s in splits.splits.items()}
    if isinstance(data, TensorFile):
        X, y = read_tensor_file(ctx, data)
    elif isinstance(data, ImagePairs):
        X, y = _image_pairs(ctx, data)
    else:
        X, y = _image_folder(ctx, data)
    X = np.ascontiguousarray(X, dtype=np.float32)
    parts = split_indices(len(X), _fractions(data.splits), ctx.seed)
    ctx.log("data_loaded", samples=len(X), **{f"rows_{k}": len(v) for k, v in parts.items()})
    return {name: (X[idx], _shape_targets(y[idx], task)) for name, idx in parts.items()}
