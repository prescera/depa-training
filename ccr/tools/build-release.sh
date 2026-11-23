#!/usr/bin/env bash
# Build one CCR family image and produce its release manifest ccr/releases/<release_id>.json
#   = {release: <spec §6 record incl. provenance>, deployment: {image, cce_policy_b64 (aci only),
#      resources: {cpu, memory_gb}, env_allowlist: [...]}}
#
# Usage (from anywhere inside the repository):
#   ccr/tools/build-release.sh --family classic --platform aci --version 2.0.0 \
#       [--repository ghcr.io/prescera] [--push] [--image <repo>@sha256:<digest>] [--debug] \
#       [--trust-bundle FILE] [--cpu 4] [--memory-gb 16] [--tiers 1,2] [--uvm-min-svn 104] [--infra-svn 4] \
#       [--out-dir ccr/releases] [--build-url URL]
#
#   --platform aci      aci-snp: generates the CCE policy with `az confcom acipolicygen -i`
#                       (stdio disabled; --debug enables stdio and debug logging) and records
#                       host_data = sha256(policy). Needs az + the confcom extension and docker.
#   --uvm-min-svn       minimum UVM SVN recorded in the release (default 104, the current production
#                       SVN per microsoft/confidential-aci-examples docs/Confidential_ACI_SCHEME.md).
#   --infra-svn         minimum SVN of Microsoft's ACI infrastructure fragment pinned in the policy (4).
#   --platform gcp      gcp-cs-tdx: the measurement is the image digest (STABLE, debug disabled).
#   --platform virtual  development only: measurement virtual_ids = ["virtual-dev"].
#   --image             skip the build and release an already-pushed digest-pinned image.
#   --push              push to --repository (CI release workflow only); adds SBOM + provenance
#                       attestations. Without --push the image is loaded into the local daemon
#                       and the digest is the locally computed manifest digest.
#
# Environment: CONFCOM_VERSION (default from ccr/policy/confcom.version) is enforced when az is
# used; SOURCE_DATE_EPOCH defaults to the HEAD commit time.
set -euo pipefail

repo_root="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
ccr_dir="${repo_root}/ccr"

family="" platform="" version="" repository="ghcr.io/prescera" push=0 image="" debug=0
trust_bundle="" cpu=4 memory_gb=16 tiers="1,2" uvm_min_svn="104" infra_svn="4" out_dir="${ccr_dir}/releases"
build_url=""

usage() { sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --family) family="$2"; shift 2 ;;
    --platform) platform="$2"; shift 2 ;;
    --version) version="$2"; shift 2 ;;
    --repository) repository="$2"; shift 2 ;;
    --push) push=1; shift ;;
    --image) image="$2"; shift 2 ;;
    --debug) debug=1; shift ;;
    --trust-bundle) trust_bundle="$(realpath "$2")"; shift 2 ;;
    --cpu) cpu="$2"; shift 2 ;;
    --memory-gb) memory_gb="$2"; shift 2 ;;
    --tiers) tiers="$2"; shift 2 ;;
    --uvm-min-svn) uvm_min_svn="$2"; shift 2 ;;
    --infra-svn) infra_svn="$2"; shift 2 ;;
    --out-dir) out_dir="$(realpath -m "$2")"; shift 2 ;;
    --build-url) build_url="$2"; shift 2 ;;
    -h|--help) usage 0 ;;
    *) echo "unknown argument: $1" >&2; usage 2 ;;
  esac
done

[[ "${family}" =~ ^(classic|torch|llm)$ ]] || { echo "--family must be classic|torch|llm" >&2; exit 2; }
case "${platform}" in
  aci) platform_id="aci-snp" ;;
  gcp) platform_id="gcp-cs-tdx" ;;
  virtual) platform_id="virtual" ;;
  *) echo "--platform must be aci|gcp|virtual" >&2; exit 2 ;;
esac
[[ "${version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][a-z0-9.]+)?$ ]] || { echo "--version must be semver" >&2; exit 2; }
[[ -z "${trust_bundle}" || "${platform}" == "aci" ]] || { echo "--trust-bundle only applies to aci" >&2; exit 2; }

suffix=$([[ ${debug} -eq 1 ]] && echo "-debug" || true)
release_id="depa-ccr-${family}-${version}-${platform}${suffix}"
[[ "${platform}" == "virtual" ]] && release_id="depa-ccr-${family}-${version}-virtual-dev"
repo_name="${repository%/}/depa-ccr-${family}"
commit="$(git -C "${repo_root}" rev-parse HEAD)"
source_url="${DEPA_SOURCE_URL:-${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-prescera/depa-training}}"
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git -C "${repo_root}" log -1 --format=%ct)}"

work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

# --- 1. image ---------------------------------------------------------------------------------
local_ref=""
if [[ -z "${image}" ]]; then
  local_ref="${repo_name}:${version}"
  build_args=(
    buildx build
    --file "${ccr_dir}/Dockerfile"
    --target "${family}"
    --build-arg "SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}"
    --label "org.opencontainers.image.revision=${commit}"
    --label "org.opencontainers.image.version=${version}"
    --metadata-file "${work}/build.json"
    --tag "${local_ref}"
  )
  if [[ ${push} -eq 1 ]]; then
    build_args+=(--provenance=mode=max --sbom=true
      --output "type=image,push=true,rewrite-timestamp=true,oci-mediatypes=true")
  else
    # Local daemons unpack the image, which BuildKit cannot combine with rewrite-timestamp;
    # local builds are for development and policy generation only.
    build_args+=(--provenance=false --load)
  fi
  echo "==> building ${local_ref} (target ${family})" >&2
  docker "${build_args[@]}" "${repo_root}" >&2
  digest="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["containerimage.digest"])' "${work}/build.json")"
  image="${repo_name}@${digest}"
fi
[[ "${image}" =~ @sha256:[0-9a-f]{64}$ ]] || { echo "image is not digest-pinned: ${image}" >&2; exit 1; }
echo "==> image ${image}" >&2

# --- 2. measurement ---------------------------------------------------------------------------
manifest_args=(
  manifest --release-id "${release_id}" --platform "${platform_id}" --family "${family}"
  --image "${image}" --tiers "${tiers}" --cpu "${cpu}" --memory-gb "${memory_gb}"
  --source "${source_url}" --commit "${commit}" --build-url "${build_url}"
  --out "${out_dir}/${release_id}.json"
)
[[ ${debug} -eq 1 ]] && manifest_args+=(--debug)

if [[ "${platform}" == "aci" ]]; then
  az_bin="${AZ:-az}"
  want="${CONFCOM_VERSION:-$(tr -d '[:space:]' <"${ccr_dir}/policy/confcom.version")}"
  have="$("${az_bin}" extension show --name confcom --query version -o tsv 2>/dev/null || true)"
  [[ "${have}" == "${want}" ]] || { echo "confcom ${want} required (found: ${have:-none}); az extension add --name confcom --version ${want}" >&2; exit 1; }

  variant=$([[ ${debug} -eq 1 ]] && echo debug || echo prod)
  stdio=$([[ ${debug} -eq 1 ]] && echo --enable-stdio || echo --disable-stdio)
  # The policy is generated from the image layers (dm-verity hashes). --omit-id keeps the
  # registry name out of the policy so mirrors of the same digest share one host_data.
  python3 "${ccr_dir}/tools/release.py" policy-input \
    --template "${ccr_dir}/policy/cce-policy-in.template.json" \
    --image "${local_ref:-${image}}" --variant "${variant}" \
    ${trust_bundle:+--trust-bundle "${trust_bundle}"} >"${work}/policy-in.json"
  echo "==> az confcom acipolicygen (${variant}, ${stdio})" >&2
  "${az_bin}" confcom acipolicygen -i "${work}/policy-in.json" ${stdio} --omit-id \
    --infrastructure-svn "${infra_svn}" --approve-wildcards --print-policy --only-show-errors \
    | tr -d '[:space:]' >"${work}/policy.b64"
  [[ -s "${work}/policy.b64" ]] || { echo "acipolicygen produced no policy" >&2; exit 1; }
  base64 -d "${work}/policy.b64" >"${work}/policy.rego"
  python3 "${ccr_dir}/tools/release.py" check-policy --policy "${work}/policy.rego" --min-svn "${infra_svn}" >&2
  manifest_args+=(--cce-policy-b64-file "${work}/policy.b64" --uvm-min-svn "${uvm_min_svn}")
  mkdir -p "${out_dir}"
  cp "${work}/policy.rego" "${out_dir}/${release_id}.rego"
fi

python3 "${ccr_dir}/tools/release.py" "${manifest_args[@]}"
