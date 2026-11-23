#!/usr/bin/env bash
# Local demo: prepare all four parties (default paths); with --inputs DIR also stage DIR/brats_<x>.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
for x in a b c d; do
  if [[ "${1:-}" == "--inputs" ]]; then bash "$HERE/prepare.sh" --party "brats-$x" --out "$2/brats_$x"
  else bash "$HERE/prepare.sh" --party "brats-$x"; fi
done
