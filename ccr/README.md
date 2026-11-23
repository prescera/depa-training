# Clean-room (CCR) images and releases

| Path | Purpose |
|---|---|
| [`Dockerfile`](Dockerfile) | Multi-stage build, targets `classic`, `torch`, `llm`. Agent venv `/opt/depa/agent` (`depa-ccr`, runs as root), trainer venv `/opt/depa/trainer` (`depa-train`, run by the agent as uid/gid 10001, path exported as `DEPA_CCR_TRAINER`). Base pinned by digest, all dependencies hash-locked, no compiler/pip/apt/setuid binaries in the runtime image, GCP launch-policy labels. |
| [`requirements/`](requirements) | `agent.txt` (SDK + depa-train core, hash-locked) and `build.txt` (hash-locked setuptools/wheel for building the in-repo packages). Trainer stacks come from `trainers/requirements/<family>.txt`. |
| [`policy/cce-policy-in.template.json`](policy/cce-policy-in.template.json) | `az confcom acipolicygen -i` input (scenario `vn2`): one container `ccr`, command `["depa-ccr","run"]`, re2 rules for the variable DEPA_* values, `DEPA_PLATFORM` fixed to `aci-snp`, no mounts. Pinned confcom version in `policy/confcom.version`. |
| [`tools/build-release.sh`](tools/build-release.sh) | Build (optionally push) a family image, resolve its digest, generate the CCE policy and `host_data` (aci), write `releases/<release_id>.json`. |
| [`releases/`](releases) | Reviewed release manifests (see its README). |

## Container contract

One container named `ccr`; image `depa-ccr-<family>@sha256:…`; command `["depa-ccr","run"]`;
work dir `/depa`; environment limited to `DEPA_CONTRACT_ID`, `DEPA_RECIPE_DIGEST`,
`DEPA_CS_URL`, `DEPA_KMS_URL`, `DEPA_PLATFORM`, optional `DEPA_TRUST_BUNDLE`,
`DEPA_LOG_LEVEL`; no privileged mode; no volume mounts. The CCE policy (aci-snp) and the
launch policy labels (gcp-cs-tdx) enforce it; the SDK job builders render it.

Measurements: `aci-snp` releases record the UVM endorsement issuer
`did:x509:0:sha256:I__iuL25oXEVFdTP_aBLx_eT1RPHbCQ_ECBQfYZpt9s::eku:1.3.6.1.4.1.311.76.59.1.2`,
feed `ContainerPlat-AMD-UVM`, `min_svn` **104** (`--uvm-min-svn`; production SVNs start at 100,
104 is current as of 2026-08-04 per
[Confidential_ACI_SCHEME.md](https://github.com/microsoft/confidential-aci-examples/blob/main/docs/Confidential_ACI_SCHEME.md)).
Every generated policy is checked to admit only Microsoft's infrastructure fragment
`mcr.microsoft.com/aci/aci-cc-infra-fragment` (issuer `…::eku:1.3.6.1.4.1.311.76.59.1.3`) with
`minimum_svn` ≥ 4 (`--infra-svn`, passed to `acipolicygen --infrastructure-svn`).

Policy variants: production disables stdio and allows `DEPA_LOG_LEVEL ∈ {info, warning,
error}`; `--debug` enables stdio and `debug`. `--trust-bundle FILE` fixes `DEPA_TRUST_BUNDLE`
in the policy so the pinned service identities are part of `host_data` (spec §12).
`--omit-id` keeps the registry name out of the policy: the same digest mirrored to another
registry keeps the same `host_data`.

## Build locally

```bash
docker buildx build -f ccr/Dockerfile --target classic -t depa-ccr-classic:dev .
# full release record (needs az + confcom for aci):
ccr/tools/build-release.sh --family classic --platform aci --version 2.0.0-dev --repository depa-local
```
