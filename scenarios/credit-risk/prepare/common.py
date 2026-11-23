"""Shared helpers for the credit-risk TDP preprocessing scripts (run at each provider)."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"


def cli(description: str, tdp: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--raw", default=str(DATA / "raw"), help="directory with the raw Home Credit CSVs")
    parser.add_argument("--out", default=str(DATA / tdp / "preprocessed"), help="the TDP's dataset directory")
    args = parser.parse_args()
    Path(args.out).mkdir(parents=True, exist_ok=True)
    return args


def aggregate(df: pd.DataFrame, by: str, spec: dict) -> pd.DataFrame:
    """groupby().agg() with flattened ``<column>_<stat>`` names."""
    out = df.groupby(by).agg(spec)
    out.columns = ["_".join(c).strip() for c in out.columns.to_flat_index()]
    return out.reset_index()
