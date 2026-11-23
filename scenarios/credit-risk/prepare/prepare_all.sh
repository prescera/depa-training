#!/usr/bin/env bash
# Local demo: prepare every party (default output paths); with --inputs DIR also stage DIR/<alias>,
# one alias per dataset as in ../recipe.yaml (bank-a contributes three datasets).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DATA="$HERE/../data"
for party in bank-a bank-b bureau fintech; do bash "$HERE/prepare.sh" --party "$party"; done
if [[ "${1:-}" == "--inputs" ]]; then
  stage() { mkdir -p "$2/$1" && cp "$DATA/$3" "$2/$1/"; }
  stage applications "$2" bank_a/preprocessed/bank_a_app_learn.parquet
  stage previous "$2" bank_a/preprocessed/bank_a_prev_agg.parquet
  stage installments "$2" bank_a/preprocessed/bank_a_inst_agg.parquet
  stage bureau "$2" bureau/preprocessed/bureau_agg.parquet
  stage pos_cash "$2" fintech/preprocessed/pos_fintech_agg.parquet
  stage credit_card "$2" bank_b/preprocessed/bank_b_cc_agg.parquet
fi
