#!/usr/bin/env bash
# Build the deployable Function package: code + hash-locked dependencies for Linux x86_64 /
# CPython 3.12 (Flex Consumption), as a deterministic zip named after its content hash.
#   ./build-package.sh [OUT_DIR]      -> prints the zip path (deploy it with deploy/scripts/deploy-brokers.sh)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
out_dir="$(realpath -m "${1:-${here}/dist}")"
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

mkdir -p "${work}/pkg" "${out_dir}"
cp "${here}/function/function_app.py" "${here}/function/host.json" "${work}/pkg/"
cp -r "${here}/function/broker" "${work}/pkg/broker"
find "${work}/pkg" -name '__pycache__' -prune -exec rm -rf {} +
python3 -m pip install --quiet --disable-pip-version-check --no-compile --no-deps --require-hashes \
  --only-binary=:all: --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 \
  --implementation cp --python-version 3.12 \
  --target "${work}/pkg/.python_packages/lib/site-packages" \
  -r "${here}/function/requirements.txt"

python3 - "${work}/pkg" "${work}/package.zip" <<'PY'
import os, sys, zipfile
root, dest = sys.argv[1], sys.argv[2]
files = sorted(os.path.relpath(os.path.join(d, f), root) for d, _, fs in os.walk(root) for f in fs)
with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
    for rel in files:
        info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
        info.external_attr = 0o644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        with open(os.path.join(root, rel), "rb") as fh:
            z.writestr(info, fh.read())
PY
sum="$(sha256sum "${work}/package.zip" | cut -c1-12)"
mv "${work}/package.zip" "${out_dir}/federation-broker-${sum}.zip"
echo "${out_dir}/federation-broker-${sum}.zip"
