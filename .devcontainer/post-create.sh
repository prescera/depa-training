#!/usr/bin/env bash
# Developer toolchain on top of the devcontainer features: pinned confcom (CCE policies),
# kubelogin (Entra-only AKS), and the SDK + trainers in editable mode.
set -euo pipefail
cd "$(dirname "$0")/.."

confcom="$(tr -d '[:space:]' < ccr/policy/confcom.version)"
az extension remove --name confcom >/dev/null 2>&1 || true
az extension add --name confcom --version "${confcom}" --yes
sudo az aks install-cli --only-show-errors >/dev/null

python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -e ./trainers -e "./sdk/python[dev]"
echo "Activate with: source venv/bin/activate"
