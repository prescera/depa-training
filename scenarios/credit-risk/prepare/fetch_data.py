"""Obtain the raw Home Credit Default Risk tables that the four TDPs start from.

``--source kaggle`` downloads the competition files with the Kaggle CLI (needs KAGGLE_USERNAME /
KAGGLE_KEY and acceptance of the competition rules). ``--source synthetic`` (default) generates
schema-faithful synthetic tables with the same file names and the columns the preprocessing uses,
with a planted default signal so models have something to learn. Synthetic data is for demos only.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

FILES = [
    "application_train.csv",
    "previous_application.csv",
    "installments_payments.csv",
    "bureau.csv",
    "bureau_balance.csv",
    "credit_card_balance.csv",
    "POS_CASH_balance.csv",
]


def fetch_kaggle(raw: Path) -> None:
    kaggle = shutil.which("kaggle")
    if kaggle is None:
        raise SystemExit("kaggle CLI not found (pip install kaggle) - or use --source synthetic")
    for name in FILES:
        if (raw / name).exists():
            continue
        subprocess.run(
            [
                kaggle,
                "competitions",
                "download",
                "-c",
                "home-credit-default-risk",
                "-f",
                name,
                "-p",
                str(raw),
            ],
            check=True,
        )
        archive = raw / f"{name}.zip"
        if archive.exists():
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(raw)
            archive.unlink()


def synthesize(raw: Path, n: int, seed: int) -> None:
    rng = np.random.default_rng(seed)
    ids = np.arange(100_002, 100_002 + n)
    income = rng.lognormal(11.8, 0.5, n).round(1)
    credit = (income * rng.uniform(1.0, 6.0, n)).round(1)
    children = rng.poisson(0.5, n)
    days_birth = -rng.integers(21 * 365, 69 * 365, n)
    days_employed = -rng.integers(0, 40 * 365, n)
    # Latent risk drives the target and the behavioural tables of the other providers.
    risk = (
        0.8 * (credit / income - 3.5) / 1.5
        - 0.6 * (days_employed / -3650.0 - 1.0)
        + 0.3 * children
        + rng.normal(0, 1.0, n)
    )
    target = (risk > np.quantile(risk, 0.9)).astype(int)
    pd.DataFrame(
        {
            "SK_ID_CURR": ids,
            "TARGET": target,
            "NAME_CONTRACT_TYPE": rng.choice(["Cash loans", "Revolving loans"], n, p=[0.9, 0.1]),
            "CODE_GENDER": rng.choice(["F", "M"], n),
            "FLAG_OWN_CAR": rng.choice(["Y", "N"], n, p=[0.35, 0.65]),
            "FLAG_OWN_REALTY": rng.choice(["Y", "N"], n, p=[0.7, 0.3]),
            "CNT_CHILDREN": children,
            "AMT_INCOME_TOTAL": income,
            "AMT_CREDIT": credit,
            "AMT_ANNUITY": (credit / rng.uniform(10, 30, n)).round(1),
            "AMT_GOODS_PRICE": (credit * rng.uniform(0.8, 1.0, n)).round(1),
            "NAME_INCOME_TYPE": rng.choice(
                ["Working", "Pensioner", "Commercial associate", "State servant"], n
            ),
            "NAME_EDUCATION_TYPE": rng.choice(["Secondary", "Higher education", "Incomplete higher"], n),
            "NAME_FAMILY_STATUS": rng.choice(["Married", "Single", "Civil marriage", "Separated"], n),
            "NAME_HOUSING_TYPE": rng.choice(["House / apartment", "With parents", "Rented apartment"], n),
            "DAYS_BIRTH": days_birth,
            "DAYS_EMPLOYED": days_employed,
            "FLAG_MOBIL": 1,
            "REGION_POPULATION_RELATIVE": rng.uniform(0.001, 0.07, n).round(6),
            "OBS_30_CNT_SOCIAL_CIRCLE": rng.poisson(1.4, n),
            "OBS_60_CNT_SOCIAL_CIRCLE": rng.poisson(1.4, n),
            "DAYS_ID_PUBLISH": -rng.integers(0, 6000, n),
        }
    ).to_csv(raw / "application_train.csv", index=False)

    def child_rows(mean: float, coverage: float) -> np.ndarray:
        """Rows per client in another provider's table (some clients absent)."""
        counts = rng.poisson(mean, n) * (rng.uniform(size=n) < coverage)
        return np.repeat(np.arange(n), counts)

    idx = child_rows(4, 0.95)
    m = len(idx)
    pd.DataFrame(
        {
            "SK_ID_PREV": np.arange(m) + 1_000_000,
            "SK_ID_CURR": ids[idx],
            "AMT_APPLICATION": (credit[idx] * rng.uniform(0.1, 1.2, m)).round(1),
            "AMT_CREDIT": (credit[idx] * rng.uniform(0.1, 1.2, m)).round(1),
            "AMT_DOWN_PAYMENT": np.where(
                rng.uniform(size=m) < 0.5, np.nan, rng.uniform(0, 20_000, m).round(1)
            ),
            "CNT_PAYMENT": rng.integers(6, 60, m).astype(float),
        }
    ).to_csv(raw / "previous_application.csv", index=False)

    idx = child_rows(12, 0.95)
    m = len(idx)
    instalment = rng.uniform(1_000, 40_000, m).round(1)
    late = rng.uniform(size=m) < 1 / (1 + np.exp(-(risk[idx] - 2.0)))
    pd.DataFrame(
        {
            "SK_ID_PREV": rng.integers(1_000_000, 2_000_000, m),
            "SK_ID_CURR": ids[idx],
            "AMT_INSTALMENT": instalment,
            "AMT_PAYMENT": (instalment * np.where(late, rng.uniform(0.3, 1.0, m), 1.0)).round(1),
            "DAYS_ENTRY_PAYMENT": np.where(late, rng.integers(1, 60, m), -rng.integers(0, 3000, m)),
        }
    ).to_csv(raw / "installments_payments.csv", index=False)

    idx = child_rows(5, 0.85)
    m = len(idx)
    bureau_ids = np.arange(m) + 5_000_000
    pd.DataFrame(
        {
            "SK_ID_CURR": ids[idx],
            "SK_ID_BUREAU": bureau_ids,
            "AMT_CREDIT_SUM": rng.lognormal(12, 1, m).round(1),
            "AMT_CREDIT_SUM_DEBT": (
                rng.lognormal(10, 1.5, m) * (1 + 0.3 * np.clip(risk[idx], 0, None))
            ).round(1),
            "AMT_CREDIT_SUM_OVERDUE": np.where(
                rng.uniform(size=m) < 0.05, rng.uniform(0, 5_000, m), 0.0
            ).round(1),
            "DAYS_CREDIT": -rng.integers(0, 3000, m),
            "DAYS_ENDDATE_FACT": -rng.integers(0, 3000, m).astype(float),
        }
    ).to_csv(raw / "bureau.csv", index=False)
    months = rng.integers(1, 12, m)
    b_idx = np.repeat(np.arange(m), months)
    overdue_p = 1 / (1 + np.exp(-(risk[idx][b_idx] - 1.5)))
    pd.DataFrame(
        {
            "SK_ID_BUREAU": bureau_ids[b_idx],
            "MONTHS_BALANCE": -rng.integers(0, 60, len(b_idx)),
            "STATUS": np.where(
                rng.uniform(size=len(b_idx)) < overdue_p,
                rng.choice(["2", "3", "4"], len(b_idx)),
                rng.choice(["0", "C", "X"], len(b_idx)),
            ),
        }
    ).to_csv(raw / "bureau_balance.csv", index=False)

    idx = child_rows(6, 0.3)
    m = len(idx)
    limit = rng.choice([45_000, 90_000, 135_000, 225_000], m).astype(float)
    pd.DataFrame(
        {
            "SK_ID_PREV": rng.integers(1_000_000, 2_000_000, m),
            "SK_ID_CURR": ids[idx],
            "AMT_BALANCE": (limit * np.clip(rng.beta(2, 3, m) + 0.1 * risk[idx], 0, 1.2)).round(1),
            "AMT_CREDIT_LIMIT_ACTUAL": limit,
            "AMT_DRAWINGS_CURRENT": rng.uniform(0, 20_000, m).round(1),
            "SK_DPD": np.where(
                rng.uniform(size=m) < 0.05 + 0.05 * (risk[idx] > 1), rng.integers(1, 90, m), 0
            ),
            "SK_DPD_DEF": np.where(rng.uniform(size=m) < 0.02, rng.integers(1, 30, m), 0),
        }
    ).to_csv(raw / "credit_card_balance.csv", index=False)

    idx = child_rows(8, 0.6)
    m = len(idx)
    pd.DataFrame(
        {
            "SK_ID_PREV": rng.integers(1_000_000, 2_000_000, m),
            "SK_ID_CURR": ids[idx],
            "MONTHS_BALANCE": -rng.integers(1, 96, m),
            "SK_DPD": np.where(
                rng.uniform(size=m) < 0.04 + 0.06 * (risk[idx] > 1), rng.integers(1, 120, m), 0
            ),
            "SK_DPD_DEF": np.where(rng.uniform(size=m) < 0.02, rng.integers(1, 30, m), 0),
        }
    ).to_csv(raw / "POS_CASH_balance.csv", index=False)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--source", choices=["synthetic", "kaggle"], default="synthetic")
    parser.add_argument("--raw", default=str(Path(__file__).resolve().parent.parent / "data" / "raw"))
    parser.add_argument("--rows", type=int, default=50_000, help="synthetic clients")
    parser.add_argument("--seed", type=int, default=2025)
    args = parser.parse_args()
    raw = Path(args.raw)
    raw.mkdir(parents=True, exist_ok=True)
    if args.source == "kaggle":
        fetch_kaggle(raw)
    else:
        synthesize(raw, args.rows, args.seed)
    print(f"raw tables in {raw}")


if __name__ == "__main__":
    main()
