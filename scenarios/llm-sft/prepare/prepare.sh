#!/usr/bin/env bash
# Non-interactive per-party preparation: prepare.sh --party <tdc|bank-a|shop-b> [--out DIR]
#   tdc    - downloads the base model HuggingFaceTB/SmolLM2-135M (Apache-2.0; safetensors + JSON only)
#            to DIR (default ../data/base_model); set BASE_MODEL_REVISION to pin a commit.
#   bank-a - generates synthetic payment notes  -> DIR (default ../data/bank_a/preprocessed)
#   shop-b - generates synthetic order notes    -> DIR (default ../data/shop_b/preprocessed)
EXTRA=()
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PARTY=""; OUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --party) PARTY="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    *) EXTRA+=("$1"); shift ;;
  esac
done
OUTARGS=()
if [[ -n "$OUT" ]]; then OUTARGS=(--out "$OUT"); fi
case "$PARTY" in
  tdc) python "$HERE/fetch_base_model.py" --revision "${BASE_MODEL_REVISION:-main}" "${OUTARGS[@]+"${OUTARGS[@]}"}" ;;
  bank-a|shop-b) python "$HERE/make_dataset.py" --tdp "${PARTY//-/_}" "${OUTARGS[@]+"${OUTARGS[@]}"}" ;;
  *) echo "usage: prepare.sh --party <tdc|bank-a|shop-b> [--out DIR]" >&2; exit 2 ;;
esac
