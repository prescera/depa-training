"""Bank A: credit applications (with the TARGET label), previous applications, instalment payments."""

from pathlib import Path

import pandas as pd
from common import aggregate, cli

args = cli(__doc__, "bank_a")
raw, out = Path(args.raw), Path(args.out)

app = pd.read_csv(
    raw / "application_train.csv",
    usecols=[
        "SK_ID_CURR",
        "TARGET",
        "NAME_CONTRACT_TYPE",
        "CODE_GENDER",
        "FLAG_OWN_CAR",
        "FLAG_OWN_REALTY",
        "CNT_CHILDREN",
        "AMT_INCOME_TOTAL",
        "AMT_CREDIT",
        "DAYS_BIRTH",
        "DAYS_EMPLOYED",
    ],
).dropna()
app["INCOME_PER_PERSON"] = app["AMT_INCOME_TOTAL"] / (app["CNT_CHILDREN"].replace({0: 1}) + 1)
app["CREDIT_TO_INCOME"] = app["AMT_CREDIT"] / app["AMT_INCOME_TOTAL"].replace({0: 1})
app["AGE_YEARS"] = -app["DAYS_BIRTH"] / 365.25
app["YEARS_EMPLOYED"] = (-app["DAYS_EMPLOYED"]).clip(lower=0) / 365.25
app[
    [
        "SK_ID_CURR",
        "TARGET",
        "INCOME_PER_PERSON",
        "CREDIT_TO_INCOME",
        "CNT_CHILDREN",
        "AGE_YEARS",
        "YEARS_EMPLOYED",
        "NAME_CONTRACT_TYPE",
        "CODE_GENDER",
        "FLAG_OWN_CAR",
        "FLAG_OWN_REALTY",
    ]
].to_parquet(out / "bank_a_app_learn.parquet", index=False)

prev = pd.read_csv(raw / "previous_application.csv")
aggregate(
    prev,
    "SK_ID_CURR",
    {
        "AMT_APPLICATION": ["count", "mean", "sum"],
        "AMT_CREDIT": ["mean", "max"],
        "AMT_DOWN_PAYMENT": ["mean"],
        "CNT_PAYMENT": ["mean"],
    },
).to_parquet(out / "bank_a_prev_agg.parquet", index=False)

inst = pd.read_csv(raw / "installments_payments.csv")
inst["PAYMENT_DIFF"] = inst["AMT_PAYMENT"] - inst["AMT_INSTALMENT"]
inst["LATE_PAYMENT"] = (inst["DAYS_ENTRY_PAYMENT"] > 0).astype(int)
aggregate(
    inst,
    "SK_ID_CURR",
    {
        "AMT_PAYMENT": ["sum", "mean"],
        "AMT_INSTALMENT": ["sum", "mean"],
        "PAYMENT_DIFF": ["mean"],
        "LATE_PAYMENT": ["sum", "mean"],
    },
).to_parquet(out / "bank_a_inst_agg.parquet", index=False)
print(f"bank_a datasets written to {out}")
