#!/usr/bin/env bash
# Local demo: prepare all parties (default paths); with --inputs DIR also stage DIR/<alias>.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
if [[ "${1:-}" == "--inputs" ]]; then
  bash "$HERE/prepare.sh" --party tdc --out "$2/base_model"
  bash "$HERE/prepare.sh" --party bank-a --out "$2/bank_a"
  bash "$HERE/prepare.sh" --party shop-b --out "$2/shop_b"
else
  for party in tdc bank-a shop-b; do bash "$HERE/prepare.sh" --party "$party"; done
fi
