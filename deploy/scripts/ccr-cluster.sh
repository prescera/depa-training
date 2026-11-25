#!/usr/bin/env bash
# Power management of the clean-room (CCR) provider's AKS cluster, located through the
# ccr-provider-cluster component state.
#
#   ccr-cluster.sh --account STATE_ACCOUNT --environment ENV status   # prints Running | Stopped | absent
#   ccr-cluster.sh --account STATE_ACCOUNT --environment ENV stop     # idempotent
#   ccr-cluster.sh --account STATE_ACCOUNT --environment ENV start    # idempotent
#   ccr-cluster.sh --account STATE_ACCOUNT --environment ENV wait-ready [MINUTES]
#        waits until the virtual node (type=virtual-kubelet) is Ready (default 20 minutes)
#
# Needs `az` logged in with rights on the cluster (stop/start: Contributor; wait-ready uses
# `az aks command invoke`, which reaches the API server through ARM).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
account="" env=""
while [[ $# -gt 1 ]]; do
  case "$1" in
    --account) account="$2"; shift 2 ;;
    --environment) env="$2"; shift 2 ;;
    *) break ;;
  esac
done
cmd="${1:-}"; shift || true
[[ -n "${account}" && -n "${env}" && "${cmd}" =~ ^(status|stop|start|wait-ready)$ ]] \
  || { echo "usage: ccr-cluster.sh --account STATE_ACCOUNT --environment ENV status|stop|start|wait-ready [MINUTES]" >&2; exit 2; }
log() { echo "[ccr-cluster] $*" >&2; }

outputs="$("${here}/state-outputs.sh" --account "${account}" --environment "${env}" ccr-provider-cluster)"
rg="$(jq -r '.resource_group_name // empty' <<<"${outputs}")"
name="$(jq -r '.cluster_name // empty' <<<"${outputs}")"
state="absent"
if [[ -n "${rg}" && -n "${name}" ]]; then
  state="$(az aks show --resource-group "${rg}" --name "${name}" --query powerState.code -o tsv 2>/dev/null || echo absent)"
fi

case "${cmd}" in
  status)
    echo "${state}" ;;
  stop)
    case "${state}" in
      absent) log "no CCR provider cluster; nothing to pause" ;;
      Stopped) log "${name} is already stopped" ;;
      *) log "stopping ${name}"; az aks stop --resource-group "${rg}" --name "${name}" --output none ;;
    esac ;;
  start)
    case "${state}" in
      absent) log "error: no CCR provider cluster (run Setup CCR Provider first)"; exit 1 ;;
      Running) log "${name} is already running" ;;
      *) log "starting ${name}"; az aks start --resource-group "${rg}" --name "${name}" --output none ;;
    esac ;;
  wait-ready)
    [[ "${state}" == Running ]] || { log "error: ${name:-cluster} is ${state}"; exit 1; }
    deadline=$(( $(date +%s) + ${1:-20} * 60 ))
    err="$(mktemp)"
    trap 'rm -f "${err}"' EXIT
    while :; do
      # command invoke runs kubectl in a pod scheduled on the cluster; when that pod cannot be
      # scheduled (e.g. the node pool is out of pod slots) the CLI fails, so keep its error.
      nodes="$(az aks command invoke --resource-group "${rg}" --name "${name}" --output tsv --query logs \
        --command "kubectl get nodes -l type=virtual-kubelet --no-headers" 2>"${err}" || true)"
      if grep -qE '[[:space:]]Ready[[:space:]]' <<<"${nodes}"; then
        log "virtual node ready: $(awk '{print $1}' <<<"${nodes}" | head -1)"
        break
      fi
      [[ -s "${err}" ]] && log "kubectl via command invoke failed: $(tail -c 400 "${err}" | tr '\n' ' ')"
      [[ -n "${nodes}" ]] && log "virtual node not Ready yet: $(head -1 <<<"${nodes}")"
      (( $(date +%s) < deadline )) || { log "error: no Ready virtual node after ${1:-20} minutes"; exit 1; }
      sleep 20
    done ;;
esac
