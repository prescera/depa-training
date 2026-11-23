#!/usr/bin/env bash
# Local demo: prepare the party (default path); with --inputs DIR also stage DIR/cifar10. Extra args pass through.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
if [[ "${1:-}" == "--inputs" ]]; then DIR="$2"; shift 2; bash "$HERE/prepare.sh" --party cifar10 --out "$DIR/cifar10" "$@"
else bash "$HERE/prepare.sh" --party cifar10 "$@"; fi
