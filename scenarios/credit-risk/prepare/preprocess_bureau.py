"""Credit bureau: bureau records and monthly balance status aggregated per client."""

from pathlib import Path

import pandas as pd
from common import aggregate, cli

args = cli(__doc__, "bureau")
raw = Path(args.raw)
bureau = pd.read_csv(raw / "bureau.csv").dropna()
balance = pd.read_csv(raw / "bureau_balance.csv", dtype={"STATUS": str}).dropna()
balance["PAST_DUE"] = balance["STATUS"].str.contains("2|3|4|5").astype(float)
bal = balance.groupby("SK_ID_BUREAU").agg(
    MONTHS_COUNT=("MONTHS_BALANCE", "count"), STATUS_PAST_DUE_RATE=("PAST_DUE", "mean")
)
bureau = bureau.merge(bal.reset_index(), how="left", on="SK_ID_BUREAU")
bureau["BUREAU_COUNT"] = 1
agg = aggregate(
    bureau,
    "SK_ID_CURR",
    {
        "AMT_CREDIT_SUM": ["mean", "max"],
        "AMT_CREDIT_SUM_DEBT": ["mean", "max"],
        "AMT_CREDIT_SUM_OVERDUE": ["mean", "max"],
        "DAYS_CREDIT": ["mean", "max"],
        "BUREAU_COUNT": ["sum"],
        "STATUS_PAST_DUE_RATE": ["mean"],
    },
)
agg.to_parquet(Path(args.out) / "bureau_agg.parquet", index=False)
print(f"bureau dataset written to {args.out}")
