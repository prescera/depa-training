"""Fintech: point-of-sale / cash loan balance behaviour aggregated per client."""

from pathlib import Path

import pandas as pd
from common import aggregate, cli

args = cli(__doc__, "fintech")
pos = pd.read_csv(Path(args.raw) / "POS_CASH_balance.csv")
aggregate(
    pos, "SK_ID_CURR", {"SK_DPD": ["max", "mean"], "SK_DPD_DEF": ["max", "mean"], "MONTHS_BALANCE": ["count"]}
).to_parquet(Path(args.out) / "pos_fintech_agg.parquet", index=False)
print(f"fintech dataset written to {args.out}")
