"""Shared TDP-side standardisation for the COVID scenario (runs at each data provider, not in the CCR)."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import pandas as pd


def sha256(value: object) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()


def standardise(
    raw: pd.DataFrame, renames: dict[str, str], keep: list[str], prefix: str, identifiers: list[str]
) -> pd.DataFrame:
    """Rename join keys, prefix all other columns with the provider name, and replace direct
    identifiers by SHA-256 tokens (``<name>_hashed``) so datasets can be joined without exposing them."""
    df = raw.rename(columns=renames)
    df = df.rename(columns={c: c if c in keep else f"{prefix}_{c}" for c in df.columns})
    for col in identifiers:
        df[f"{col}_hashed"] = df[col].map(sha256)
    return df.drop(columns=identifiers)


def cli(description: str, tdp: str, default_input: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    data = Path(__file__).resolve().parent.parent / "data"
    parser.add_argument("--input", default=str(data / default_input), help="raw CSV export")
    parser.add_argument(
        "--out", default=str(data / tdp / "preprocessed"), help="the TDP's plaintext dataset directory"
    )
    return parser.parse_args()
