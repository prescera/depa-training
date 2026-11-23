#!/usr/bin/env bash
# Key material for the fixed Azure pilot parties (see pilot-parties.py), kept in the GitHub
# environment secret DEPA_PARTY_KEYS as base64(tar.gz) of <party_id>/{signing.jwk,hpke.jwk,party.json}.
#
#   party-keys.sh init DIR                          generate keys for every pilot party (depa party keygen)
#   party-keys.sh pack DIR                          print base64(tar.gz) of DIR (all parties must be complete)
#   party-keys.sh publish DIR --env ENV --repo REPO pack and store as the DEPA_PARTY_KEYS environment secret
#   party-keys.sh unpack DIR                        restore from $DEPA_PARTY_KEYS (used by the workflows)
set -euo pipefail

# party_id|roles — must match pilot-parties.py.
PARTIES=(
  "dc-member-1|dc-member"
  "tdc|tdc,tdp"
  "tdp-1|tdp"
  "tdp-2|tdp"
  "tdp-3|tdp"
  "tdp-4|tdp"
)
FILES=(signing.jwk hpke.jwk party.json)

die() { echo "party-keys: $*" >&2; exit 1; }
usage() { sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

check_complete() {
  local dir="$1" entry party file
  [[ -d "${dir}" ]] || die "${dir} does not exist (run: party-keys.sh init ${dir})"
  for entry in "${PARTIES[@]}"; do
    party="${entry%%|*}"
    for file in "${FILES[@]}"; do
      [[ -s "${dir}/${party}/${file}" ]] || die "${dir}/${party}/${file} is missing or empty"
    done
  done
}

# Writes the archive to $2; prints nothing, so a failure never yields partial output.
pack_to() {
  local dir="$1" out="$2" parties=()
  check_complete "${dir}"
  for entry in "${PARTIES[@]}"; do parties+=("${entry%%|*}"); done
  tar -C "${dir}" --sort=name --owner=0 --group=0 --numeric-owner -czf - "${parties[@]}" | base64 -w0 >"${out}"
  [[ -s "${out}" ]] || die "empty archive"
}

cmd="${1:-}"; [[ $# -ge 2 ]] || usage
dir="$2"; shift 2
case "${cmd}" in
  init)
    command -v depa >/dev/null || die "the depa CLI is not installed (pip install ./sdk/python from the repository root)"
    for entry in "${PARTIES[@]}"; do
      [[ ! -e "${dir}/${entry%%|*}" ]] || die "${dir}/${entry%%|*} already exists; refusing to overwrite keys"
    done
    install -d -m 0700 "${dir}"
    home="$(mktemp -d)"
    trap 'rm -rf "${home}"' EXIT
    for entry in "${PARTIES[@]}"; do
      party="${entry%%|*}"
      DEPA_HOME="${home}" depa party keygen --party-id "${party}" --name "Pilot ${party}" \
        --roles "${entry#*|}" --no-set-default >/dev/null
      mv "${home}/keys/${party}" "${dir}/${party}"
    done
    chmod -R go-rwx "${dir}"
    check_complete "${dir}"
    echo "created keys for ${#PARTIES[@]} parties in ${dir}" >&2
    ;;
  pack)
    tmp="$(mktemp)"
    trap 'rm -f "${tmp}"' EXIT
    pack_to "${dir}" "${tmp}"
    cat "${tmp}"
    ;;
  publish)
    env="" repo=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --env) env="$2"; shift 2 ;;
        --repo) repo="$2"; shift 2 ;;
        *) usage ;;
      esac
    done
    [[ -n "${env}" && -n "${repo}" ]] || usage
    command -v gh >/dev/null || die "the GitHub CLI (gh) is not installed"
    tmp="$(umask 077 && mktemp)"
    trap 'rm -f "${tmp}"' EXIT
    pack_to "${dir}" "${tmp}"
    gh secret set DEPA_PARTY_KEYS --env "${env}" --repo "${repo}" <"${tmp}"
    echo "published DEPA_PARTY_KEYS ($(wc -c <"${tmp}") bytes) to ${repo} environment ${env}" >&2
    ;;
  unpack)
    [[ -n "${DEPA_PARTY_KEYS:-}" ]] || die "DEPA_PARTY_KEYS is empty (re-publish it: party-keys.sh publish DIR --env ENV --repo REPO)"
    install -d -m 0700 "${dir}"
    base64 -d <<<"${DEPA_PARTY_KEYS}" | tar -C "${dir}" --no-same-owner -xzf -
    chmod -R go-rwx "${dir}"
    check_complete "${dir}"
    ;;
  *) usage ;;
esac
