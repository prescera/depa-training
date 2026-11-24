# Credit default risk

| Task | Tier / family | Privacy | Parties | Data | Output |
|---|---|---|---|---|---|
| Binary classification (XGBoost) over six joined tables | 1 / `classic` | DP (dp-xgboost), ε = 4.0 | TDC + 4 TDPs | Home Credit schema (synthetic by default) | XGBoost JSON model |

A fintech (the TDC) wants a default-risk model that no single institution could train alone:
- **Bank A**: credit applications, previous applications and payment instalments;
- **Bank B**: credit card balances;
- **Credit bureau**: bureau records;
- **Fintech**: POS and cash loan balances.

Today this needs bilateral data-sharing agreements and copies of customer data. Here each
institution keeps its tables, signs one contract and can revoke at any time. The join and training
run only inside an attested clean room.

## Parties and datasets

| Alias | Dataset id | Provider |
|---|---|---|
| `applications` | `bank-a/credit-applications` | Bank A |
| `previous` | `bank-a/previous-applications` | Bank A |
| `installments` | `bank-a/payment-installments` | Bank A |
| `credit_card` | `bank-b/credit-card-balance` | Bank B |
| `bureau` | `bureau/bureau-records` | Credit bureau |
| `pos_cash` | `fintech/pos-cash-balance` | Fintech |

`prepare/prepare.sh --party <bank-a|bank-b|bureau|fintech>` builds each provider's Parquet files.
The data is synthetic and follows the
[Home Credit Default Risk](https://www.kaggle.com/c/home-credit-default-risk) schema. It is
deterministic, so providers generate consistent client ids independently. Set
`FETCH_SOURCE=kaggle` with `KAGGLE_USERNAME`/`KAGGLE_KEY` to use the real competition data.

## Recipe

[`recipe.yaml`](recipe.yaml) runs four steps:
1. `sql/duckdb@1` left-joins the six tables on `SK_ID_CURR`, selecting only contracted columns;
2. `tabular/split@1` makes stratified splits with declared bounds (no fitting on private data);
3. `xgboost/train@1` trains with dp-xgboost (pure DP, so δ spent is 0);
4. `eval/classification@1` reports accuracy and ROC AUC.

## Run it

```bash
depa dev up && depa dev scenario run credit-risk

bash prepare/prepare_all.sh --inputs /tmp/credit/inputs
depa-train run --recipe recipe.yaml --inputs /tmp/credit/inputs --outputs /tmp/credit/out --result /tmp/credit/result.json
```

On Azure, run the **End-to-End demo** workflow (or **Run Scenario** on an existing deployment) with scenario `credit-risk`. This is also the scenario
used by the pull-request demo check.

Verified result (synthetic data, CPU, about 4 s): accuracy 0.95, ROC AUC 0.975 (0.984 without DP),
with ε 4.0 spent.
