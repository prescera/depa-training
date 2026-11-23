#!/usr/bin/env bash
# Non-interactive per-party preparation: prepare.sh --party <brats-a|brats-b|brats-c|brats-d> [--out DIR]
# Unpacks the bundled BraTS 2020 sample slices (../data/brats_<X>.tar.gz); set NIFTI_DIR to convert the
# provider's own NIfTI volumes instead. Default DIR: ../data/brats_<X>/preprocessed.
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
  brats-a|brats-b|brats-c|brats-d) TDP="$(echo "${PARTY#brats-}" | tr 'a-z' 'A-Z')" ;;
  *) echo "usage: prepare.sh --party <brats-a|brats-b|brats-c|brats-d> [--out DIR]" >&2; exit 2 ;;
esac
python "$HERE/prepare_brats.py" --tdp "$TDP" ${NIFTI_DIR:+--nifti-dir "$NIFTI_DIR"} "${OUTARGS[@]+"${OUTARGS[@]}"}"
