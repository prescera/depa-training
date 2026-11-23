#!/usr/bin/env bash
# Non-interactive per-party preparation: prepare.sh --party <bank-a|bank-b|bureau|fintech> [--out DIR]
# Raw tables: FETCH_SOURCE=synthetic (default; deterministic, so every party generates the same
# client ids independently) or FETCH_SOURCE=kaggle (needs KAGGLE_USERNAME/KAGGLE_KEY). Writes the
# party's parquet dataset(s) to DIR (default ../data/<party_with_underscore>/preprocessed).
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
  bank-a|bank-b|bureau|fintech) ;;
  *) echo "usage: prepare.sh --party <bank-a|bank-b|bureau|fintech> [--out DIR]" >&2; exit 2 ;;
esac
python "$HERE/fetch_data.py" --source "${FETCH_SOURCE:-synthetic}" ${FETCH_ROWS:+--rows "$FETCH_ROWS"}
python "$HERE/preprocess_${PARTY//-/_}.py" "${OUTARGS[@]+"${OUTARGS[@]}"}"
