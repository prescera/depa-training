#!/usr/bin/env bash
# Local demo: prepare the party (default path); with --inputs DIR also stage DIR/mnist. Extra args pass through.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
if [[ "${1:-}" == "--inputs" ]]; then DIR="$2"; shift 2; bash "$HERE/prepare.sh" --party mnist --out "$DIR/mnist" "$@"
else bash "$HERE/prepare.sh" --party mnist "$@"; fi
