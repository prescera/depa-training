# DEPA Training v2 — infrastructure as code

Terraform for the data and compute planes. The governance plane (KMS, Contract Service on
Azure Confidential Ledger) lives in the service repositories
(`depa-training-kms-azure/deploy/terraform`, `contract-service/deploy/terraform`) and is
composed here only in [`environments/dc`](environments/dc).

## Modules

| Module | Plane / party | Provisions |
|---|---|---|
| [`azure/ccr-aks-vn2`](modules/azure/ccr-aks-vn2) (+ `cluster/`) | compute · TDC/CCRP | AKS + virtual nodes v2 → confidential ACI (`aci-snp`), delegated subnet with egress NSG, scoped identities, job-submitter RBAC |
| [`gcp/ccr-confidential-space`](modules/gcp/ccr-confidential-space) (+ `instance-template/`) | compute · TDC/CCRP | Confidential Space on C3 TDX (`gcp-cs-tdx`): workload SA, VPC/NAT, egress firewall, Private Google Access DNS, job-submitter role; per-run instance template |
| [`aws/tdp-storage`](modules/aws/tdp-storage), [`azure/tdp-storage`](modules/azure/tdp-storage), [`gcp/tdp-storage`](modules/gcp/tdp-storage) | data · TDP | Private, versioned, encrypted dataset storage; read access federated to the DC KMS issuer for `sub = dataset:<id>` |
| [`aws/tdc-output-storage`](modules/aws/tdc-output-storage), [`azure/tdc-output-storage`](modules/azure/tdc-output-storage), [`gcp/tdc-output-storage`](modules/gcp/tdc-output-storage) | data · TDC | Output storage; write-only access for `sub = output:<party_id>` |
| [`azure/federation-broker`](modules/azure/federation-broker) | data · TDP/TDC | Interim Azure path (spec §10.1): Function that verifies ES256 KMS tokens and returns container-scoped user-delegation SAS; default `federation_mode` of the Azure storage modules |
| `*/federated-storage` | internal | Shared implementation of the two storage wrappers |

## Environments (examples; copy and adapt)

| Environment | Role | Apply order |
|---|---|---|
| [`tdp-aws`](environments/tdp-aws), [`tdp-azure`](environments/tdp-azure), [`tdp-gcp`](environments/tdp-gcp) | TDP | single apply, then register datasets with the DC |
| [`tdc-azure-aks`](environments/tdc-azure-aks) | TDC / CCRP | `infra/` then `cluster/` |
| [`tdc-gcp-cs`](environments/tdc-gcp-cs) | TDC / CCRP | single apply |
| [`azure-single-subscription`](environments/azure-single-subscription) | whole collaborative, one Azure subscription | one root per component: `kms`, `contract-service`, `registry`, `data-provider` ×4, `data-consumer`, `ccr-provider-cluster`, `ccr-provider-config` (see [`../README.md`](../README.md)) |
| [`dc`](environments/dc) | DC | `1-core/` → manual ACL Private Link Service → `2-gateway/` → app bundle + bootstrap |

The DC must be up first (its OIDC issuer URL is an input to every storage module).

## Conventions

Terraform `>= 1.9, < 2`; providers pinned with `~>` (azurerm 4.x — mirroring the production
DEPA-inferencing stack —, aws 6.x, google 8.x, helm/kubernetes 3.x). Modules never configure
providers; environments do and carry a commented remote-backend block. Everything is tagged /
labelled `workload = depa-training`, `component = <module>`, `managed_by = terraform`.

## Static checks

```bash
make validate                      # terraform fmt -check, init -backend=false, validate, test
make validate ONLY=modules/gcp     # a sub-tree
DEPA_KMS_AZURE_DIR=~/src/depa-training-kms-azure DEPA_CONTRACT_SERVICE_DIR=~/src/contract-service \
  make validate ONLY=environments/dc
```

Nothing here contacts cloud APIs or remote state.
