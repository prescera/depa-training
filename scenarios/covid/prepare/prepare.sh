#!/usr/bin/env bash
# Non-interactive per-party preparation: prepare.sh --party <icmr|cowin|index> [--out DIR]
# Reads the party's raw export from ../data/<party>/ (bundled synthetic POC data) and writes its
# tokenised dataset to DIR (default ../data/<party>/preprocessed, the dataset descriptor source path).
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
  icmr|cowin|index) python "$HERE/preprocess_$PARTY.py" "${OUTARGS[@]+"${OUTARGS[@]}"}" ;;
  *) echo "usage: prepare.sh --party <icmr|cowin|index> [--out DIR]" >&2; exit 2 ;;
esac
