#!/usr/bin/env bash
# Local demo: prepare every party (default output paths); with --inputs DIR also stage DIR/<alias>.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
for party in icmr cowin index; do
  if [[ "${1:-}" == "--inputs" ]]; then bash "$HERE/prepare.sh" --party "$party" --out "$2/$party"
  else bash "$HERE/prepare.sh" --party "$party"; fi
done
