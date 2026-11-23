"""Bank B: credit-card balance behaviour aggregated per client."""

from pathlib import Path

import pandas as pd
from common import aggregate, cli

args = cli(__doc__, "bank_b")
cc = pd.read_csv(Path(args.raw) / "credit_card_balance.csv")
aggregate(
    cc,
    "SK_ID_CURR",
    {
        "AMT_BALANCE": ["mean", "max"],
        "AMT_CREDIT_LIMIT_ACTUAL": ["mean", "max"],
        "AMT_DRAWINGS_CURRENT": ["mean", "max"],
        "SK_DPD": ["max", "mean"],
        "SK_DPD_DEF": ["max", "mean"],
    },
).to_parquet(Path(args.out) / "bank_b_cc_agg.parquet", index=False)
print(f"bank_b dataset written to {args.out}")
