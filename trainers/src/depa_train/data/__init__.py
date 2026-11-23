"""Data source configs (import-light) and safe readers (import numpy/pyarrow lazily)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from ..types import Alias, ColumnName, RelGlob, Splits, StepRef, Strict

TableFormat = Literal["csv", "parquet", "jsonl", "arrow"]
TABLE_SUFFIXES: dict[str, frozenset[str]] = {
    "csv": frozenset({".csv", ".csv.gz"}),
    "parquet": frozenset({".parquet"}),
    "jsonl": frozenset({".jsonl", ".ndjson", ".jsonl.gz"}),
    "arrow": frozenset({".arrow", ".feather"}),
}


class FileTable(Strict):
    """Files of one input dataset read as a single table (all files must share a schema)."""

    input: Alias
    files: RelGlob = "*"
    format: TableFormat
    columns: list[ColumnName] | None = Field(default=None, min_length=1, max_length=4096)


TableSource = StepRef | FileTable


class TensorFile(Strict):
    """Feature/target arrays stored in one safetensors or HDF5 file of an input dataset."""

    kind: Literal["tensors"]
    input: Alias
    file: RelGlob
    format: Literal["safetensors", "hdf5"]
    features_key: str = Field(default="features", pattern=r"^[A-Za-z0-9_./-]{1,128}$")
    targets_key: str = Field(default="targets", pattern=r"^[A-Za-z0-9_./-]{1,128}$")
    splits: Splits
    max_samples: int | None = Field(default=None, ge=1)


class ImagePairs(Strict):
    """Image/mask pairs (segmentation), one sample directory per case, across several inputs."""

    kind: Literal["image_pairs"]
    inputs: list[Alias] = Field(min_length=1, max_length=64)
    samples: RelGlob = Field(description="glob of sample directories inside each input")
    image: RelGlob = Field(description="glob of the image file inside a sample directory")
    mask: RelGlob = Field(description="glob of the mask file inside a sample directory")
    format: Literal["png", "nifti"] = "png"
    mask_threshold: float = 0.0
    skip_empty_masks: bool = False
    splits: Splits
    max_samples: int | None = Field(default=None, ge=1)


class ImageFolder(Strict):
    """Classification images in one sub-directory per class (classes are declared, hence public)."""

    kind: Literal["image_folder"]
    inputs: list[Alias] = Field(min_length=1, max_length=64)
    classes: list[str] = Field(min_length=2, max_length=10_000)
    size: tuple[Annotated[int, Field(ge=1, le=4096)], Annotated[int, Field(ge=1, le=4096)]] = Field(
        description="height, width after resizing"
    )
    grayscale: bool = False
    splits: Splits
    max_samples: int | None = Field(default=None, ge=1)


class TabularRef(Strict):
    """Feature matrices from a ``tabular/split`` step."""

    kind: Literal["tabular"]
    step: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")


ArrayData = Annotated[TabularRef | TensorFile | ImagePairs | ImageFolder, Field(discriminator="kind")]
