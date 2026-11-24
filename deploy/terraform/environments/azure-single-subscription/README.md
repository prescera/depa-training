# Environment: single-subscription Azure deployment (demo)

A whole Data Collaborative in one Azure subscription, one resource group per party, split into
independent Terraform roots: one root and one state per component, so each party's part can be
deployed, re-deployed or destroyed on its own.

| Root | State key (`depa-training/<environment>/…`) | Creates | Reads |
|---|---|---|---|
| [`kms`](kms) | `kms.tfstate` | `<prefix>-dc-kms-rg`: KMS ledger (Azure Confidential Ledger), Key Vault + member certificate, network | — |
| [`contract-service`](contract-service) | `contract-service.tfstate` | `<prefix>-dc-cs-rg`: Contract Service ledger, Key Vault, network | — |
| [`registry`](registry) | `registry.tfstate` | `<prefix>-registry-rg`: container registry for clean-room images, AcrPush for the deployer | — |
| [`data-provider`](data-provider) (`party_id` = tdp-1…tdp-4) | `data-provider-<party>.tfstate` | `<prefix>-<party>-rg`: dataset storage (`datasets` container) + SAS broker | `kms` (issuer) + pinned KMS JWKS |
| [`data-consumer`](data-consumer) | `data-consumer.tfstate` | `<prefix>-tdc-rg`: the consumer's own dataset storage (llm-sft base model), output storage, SAS brokers | `kms` + pinned KMS JWKS |
| [`ccr-provider-cluster`](ccr-provider-cluster) | `ccr-provider-cluster.tfstate` | `<prefix>-ccr-provider-rg`: AKS + virtual nodes infrastructure, registry pull identity, Log Analytics | `registry` (optional) |
| [`ccr-provider-config`](ccr-provider-config) | `ccr-provider-config.tfstate` | virtual nodes v2 Helm release (confidential ACI), clean-room namespace, quota, RBAC | `ccr-provider-cluster` |

- Names come from [`modules/naming`](modules/naming): a deterministic suffix
  `substr(sha1("<subscription>/<prefix>"), 0, 5)` shared by every root, so no root needs another's
  state just for names. Cross-root values use `terraform_remote_state` (variable `state_backend`)
  or, for the KMS JWKS pinned into the SAS brokers, `deploy/scripts/fetch-kms-jwks.sh`.
- Apply order: `kms`, `contract-service` (→ bundles + bootstrap) → `registry` → `data-provider`
  ×4, `data-consumer`, `ccr-provider-cluster` → `ccr-provider-config`. Destroy in reverse.
- After applying `data-provider` / `data-consumer`, deploy the broker code:
  `deploy/scripts/deploy-brokers.sh --package "$(../../modules/azure/federation-broker/build-package.sh)" --from-json <(terraform output -json brokers)`.
- `deploy/scripts/render-env.sh` reads every component's outputs from the state blobs.
- Each root has offline plan tests (`tests/plan.tftest.hcl`, remote state mocked with `override_data`).

The GitHub workflows do all of this; see [`deploy/README.md`](../../../README.md).
