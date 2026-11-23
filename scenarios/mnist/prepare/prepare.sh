#!/usr/bin/env bash
# Non-interactive preparation for the single party: prepare.sh --party mnist [--out DIR] [--max-samples N]
# Downloads the public dataset (checksum-verified, no pickle) and writes the TDP dataset to DIR
# (default ../data/preprocessed).
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
if [[ "$PARTY" != "mnist" ]]; then echo "usage: prepare.sh --party mnist [--out DIR] [--max-samples N]" >&2; exit 2; fi
python "$HERE/preprocess_mnist.py" "${OUTARGS[@]+"${OUTARGS[@]}"}" "${EXTRA[@]+"${EXTRA[@]}"}"
