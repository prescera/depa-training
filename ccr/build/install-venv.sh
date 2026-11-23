#!/bin/sh
# Create a venv and install a hash-locked requirement set plus in-repo wheels into it, then
# drop pip and precompile bytecode deterministically. Used only at image build time.
#   install-venv.sh <venv-dir> <requirements.txt> <wheel>...
set -eu
venv="$1"; requirements="$2"; shift 2

python -m venv "${venv}"
"${venv}/bin/pip" install --require-hashes --no-deps -r "${requirements}"
"${venv}/bin/pip" install --no-deps --no-index "$@"
# Only pip is removed: the classic stack needs setuptools (pkg_resources) at run time.
"${venv}/bin/pip" uninstall -y pip
python -m compileall -q -j 0 --invalidation-mode unchecked-hash "${venv}"
