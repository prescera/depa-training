"""Safe readers for tabular and array inputs. Only non-executable formats are supported."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pyarrow as pa

from ..errors import StepError
from ..types import StepRef
from . import TABLE_SUFFIXES, FileTable, TableSource, TensorFile

if TYPE_CHECKING:
    from ..context import StepContext

MAX_TABLE_ROWS = 200_000_000


def read_table_files(paths: list[Path], fmt: str) -> pa.Table:
    tables = []
    for path in paths:
        if fmt == "csv":
            import pyarrow.csv as pacsv

            tables.append(pacsv.read_csv(path))
        elif fmt == "parquet":
            import pyarrow.parquet as pq

            tables.append(pq.read_table(path))
        elif fmt == "jsonl":
            import pyarrow.json as pajson

            tables.append(pajson.read_json(path))
        elif fmt == "arrow":
            from pyarrow import feather

            tables.append(feather.read_table(path))
        else:  # pragma: no cover - guarded by the config schema
            raise StepError(f"unsupported table format '{fmt}'")
    try:
        table = pa.concat_tables(tables, promote_options="permissive") if len(tables) > 1 else tables[0]
    except (pa.ArrowInvalid, pa.ArrowTypeError):
        raise StepError("input files do not share a compatible schema") from None
    if table.num_rows > MAX_TABLE_ROWS:
        raise StepError("input table exceeds the row limit")
    return table


def read_file_table(ctx: StepContext, spec: FileTable) -> pa.Table:
    paths = ctx.files(spec.input, spec.files, suffixes=TABLE_SUFFIXES[spec.format])
    table = read_table_files(paths, spec.format)
    if spec.columns is not None:
        missing = [c for c in spec.columns if c not in table.column_names]
        if missing:
            raise StepError(f"input '{spec.input}' lacks columns {missing}")
        table = table.select(spec.columns)
    return table


def load_table(ctx: StepContext, source: TableSource) -> pa.Table:
    if isinstance(source, StepRef):
        table = ctx.artifact(source)
        if not isinstance(table, pa.Table):
            raise StepError(f"step '{source.step}' did not produce a table")
        return table
    return read_file_table(ctx, source)


def read_tensor_file(ctx: StepContext, spec: TensorFile) -> tuple[np.ndarray, np.ndarray]:
    suffixes = frozenset({".safetensors"}) if spec.format == "safetensors" else frozenset({".h5", ".hdf5"})
    paths = ctx.files(spec.input, spec.file, suffixes=suffixes)
    if len(paths) != 1:
        raise StepError(f"'{spec.file}' must match exactly one file in input '{spec.input}'")
    if spec.format == "safetensors":
        from safetensors.numpy import load_file

        tensors = load_file(str(paths[0]))
        if spec.features_key not in tensors or spec.targets_key not in tensors:
            raise StepError("safetensors file lacks the configured feature/target keys")
        features, targets = tensors[spec.features_key], tensors[spec.targets_key]
    else:
        import h5py

        with h5py.File(paths[0], "r") as fh:
            for key in (spec.features_key, spec.targets_key):
                if key not in fh or not isinstance(fh[key], h5py.Dataset):
                    raise StepError("HDF5 file lacks the configured feature/target datasets")
                if fh[key].dtype.kind not in "biuf":
                    raise StepError("HDF5 datasets must be numeric")
            features, targets = fh[spec.features_key][...], fh[spec.targets_key][...]
    if len(features) != len(targets):
        raise StepError("features and targets have different lengths")
    if spec.max_samples is not None:
        features, targets = features[: spec.max_samples], targets[: spec.max_samples]
    return np.ascontiguousarray(features), np.ascontiguousarray(targets)


def split_indices(n: int, fractions: dict[str, float], seed: int) -> dict[str, np.ndarray]:
    """Deterministic random partition of ``range(n)`` into named splits (empty splits omitted)."""
    order = np.random.default_rng(seed).permutation(n)
    out: dict[str, np.ndarray] = {}
    start = 0
    names = [k for k, v in fractions.items() if v > 0]
    for i, name in enumerate(names):
        end = n if i == len(names) - 1 else start + round(fractions[name] * n)
        out[name] = np.sort(order[start:end])
        start = end
    if any(len(v) == 0 for v in out.values()):
        raise StepError("a configured split is empty; use more data or larger fractions")
    return out
