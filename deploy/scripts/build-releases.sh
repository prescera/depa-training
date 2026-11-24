#!/usr/bin/env bash
# Build and push CCR images to the deployment's registry, generate their CCE policies and write the
# release manifests (aci-snp + virtual-dev) for every requested family.
#
#   build-releases.sh --registry <login-server> --version <semver> [--families classic,torch,llm]
#                     [--out-dir DIR] [--build-url URL]
#
# Needs docker buildx, az (logged in, AcrPush on the registry) with the pinned confcom extension.
set -euo pipefail
repo_root="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
registry="" version="" families="classic" out_dir="${repo_root}/ccr/releases" build_url=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --registry) registry="$2"; shift 2 ;;
    --version) version="$2"; shift 2 ;;
    --families) families="$2"; shift 2 ;;
    --out-dir) out_dir="$(realpath -m "$2")"; shift 2 ;;
    --build-url) build_url="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n "${registry}" && -n "${version}" ]] || { echo "--registry and --version are required" >&2; exit 2; }

az acr login --name "${registry%%.*}" >&2
IFS=',' read -ra fams <<<"${families}"
for family in "${fams[@]}"; do
  common=(--family "${family}" --version "${version}" --out-dir "${out_dir}" --build-url "${build_url}")
  "${repo_root}/ccr/tools/build-release.sh" "${common[@]}" --platform aci --repository "${registry}" --push
  image="$(jq -er '.deployment.image' "${out_dir}/depa-ccr-${family}-${version}-aci.json")"
  "${repo_root}/ccr/tools/build-release.sh" "${common[@]}" --platform virtual --image "${image}"
done
ls -1 "${out_dir}"
