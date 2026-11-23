#!/usr/bin/env bash
# Print the KMS OIDC JWKS to pin into the federation brokers, after checking that the OIDC
# endpoint and GET /info (both served by the attested CCF app, TLS pinned to the ledger
# identity) publish the same key set (spec §10).
#   fetch-kms-jwks.sh <kms-ledger-name>
set -euo pipefail
ledger="${1:?usage: fetch-kms-jwks.sh <kms-ledger-name>}"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

curl -sSf "https://identity.confidential-ledger.core.azure.com/ledgerIdentity/${ledger}" \
  | jq -er '.ledgerTlsCertificate' >"${tmp}/ledger.pem"
base="https://${ledger}.confidential-ledger.azure.com/app"
curl -sSf --cacert "${tmp}/ledger.pem" "${base}/oidc/jwks" | jq -cS '{keys: (.keys | sort_by(.kid))}' >"${tmp}/oidc.json"
curl -sSf --cacert "${tmp}/ledger.pem" "${base}/info" | jq -cS '{keys: (.oidc_jwks | sort_by(.kid))}' >"${tmp}/info.json"
cmp -s "${tmp}/oidc.json" "${tmp}/info.json" \
  || { echo "fetch-kms-jwks: /oidc/jwks and GET /info oidc_jwks differ" >&2; exit 1; }
jq -e '[.keys[] | select(.kty == "EC" and .crv == "P-256")] | length > 0' "${tmp}/oidc.json" >/dev/null \
  || { echo "fetch-kms-jwks: no ES256 key in the KMS OIDC JWKS (is the KMS bootstrapped?)" >&2; exit 1; }
cat "${tmp}/oidc.json"
