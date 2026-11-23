# CCR release manifests

`<release_id>.json` = `{release: <spec §6 record incl. provenance>, deployment: {image,
cce_policy_b64 (aci-snp only), resources: {cpu, memory_gb}, env_allowlist}}`, consumed by the
`depa` SDK job submitters (`depa.deploy.release`) and submitted verbatim (the `release`
object) in `approve_ccr_release` proposals to **both** the KMS and the CS. `aci-snp` releases
ship the decoded policy next to the manifest as `<release_id>.rego` for review.

Manifests are produced only by the tag-triggered `Release` workflow
(`.github/workflows/release.yml` → `ccr/tools/build-release.sh --push`) from images pushed to
GHCR, and committed here from the draft GitHub release after review. Never hand-edit them:
`release_id`s are immutable once approved (spec §6).

Per family and version the workflow emits:

| release_id | platform | measurement |
|---|---|---|
| `depa-ccr-<family>-<v>-aci` | `aci-snp` | `host_data` = sha256(CCE policy, stdio disabled) + UVM endorsement (did/feed/min_svn) |
| `depa-ccr-<family>-<v>-aci-debug` | `aci-snp` | same image, policy with stdio enabled and debug logging — never approve for production datasets |
| `depa-ccr-<family>-<v>-gcp` | `gcp-cs-tdx` | image digest, `support_attributes = [STABLE]`, `require_debug_disabled` |
| `depa-ccr-<family>-<v>-virtual-dev` | `virtual` | `virtual_ids = [virtual-dev]` (sandbox only) |

## Local development manifest

For the local sandbox (`allow_virtual_attestation = true`), build the image locally and write
a `virtual` manifest pointing at the local digest:

```bash
ccr/tools/build-release.sh --family classic --platform virtual --version 2.0.0-dev \
    --repository depa-local --out-dir /tmp/depa-releases
```

The digest is specific to your daemon's build, so such manifests are not committed.
