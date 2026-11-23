#!/usr/bin/env bash
# Move party key directories (<party_id>/{signing.jwk,hpke.jwk,party.json}, as written by the
# depa CLI) in and out of the single GitHub secret DEPA_PARTY_KEYS.
#   party-keys.sh pack <keys-root>     # prints base64(tar.gz) -> gh secret set DEPA_PARTY_KEYS --env <env>
#   party-keys.sh unpack <keys-root>   # reads base64(tar.gz) from $DEPA_PARTY_KEYS
set -euo pipefail
cmd="${1:?usage: party-keys.sh pack|unpack <keys-root>}"
root="${2:?usage: party-keys.sh pack|unpack <keys-root>}"
case "${cmd}" in
  pack)
    tar -C "${root}" --sort=name --owner=0 --group=0 --numeric-owner -czf - . | base64 -w0
    ;;
  unpack)
    [[ -n "${DEPA_PARTY_KEYS:-}" ]] || { echo "DEPA_PARTY_KEYS is empty" >&2; exit 1; }
    install -d -m 0700 "${root}"
    base64 -d <<<"${DEPA_PARTY_KEYS}" | tar -C "${root}" --no-same-owner -xzf -
    chmod -R go-rwx "${root}"
    ;;
  *) echo "usage: party-keys.sh pack|unpack <keys-root>" >&2; exit 2 ;;
esac
