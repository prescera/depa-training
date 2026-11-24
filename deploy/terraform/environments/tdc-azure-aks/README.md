# Environment: TDC compute on AKS + virtual nodes v2 (confidential ACI, `aci-snp`)

Two phases with separate state (the Kubernetes/Helm providers must not be configured from a
cluster created in the same apply):

| Phase | Uses | Creates |
|---|---|---|
| [`infra/`](infra) | [`modules/azure/ccr-aks-vn2`](../../modules/azure/ccr-aks-vn2), [`modules/azure/tdc-output-storage`](../../modules/azure/tdc-output-storage) | RGs, VNet + delegated subnet + egress NSG, AKS, identities, Log Analytics, output storage writable for `output:<party_id>` via the federation broker |
| [`cluster/`](cluster) | [`modules/azure/ccr-aks-vn2/cluster`](../../modules/azure/ccr-aks-vn2/cluster) | Virtual nodes v2 Helm release, `depa-ccr` namespace, quota, job-submitter RBAC |

## Prerequisites

- `az login` as a principal with Owner (or Contributor + User Access Administrator) on the
  subscription — role assignments are created — and `kubelogin` on PATH for phase 2.
- Providers registered: `Microsoft.ContainerService`, `Microsoft.ContainerInstance`,
  `Microsoft.Network`, `Microsoft.ManagedIdentity`, `Microsoft.OperationalInsights`.
- The region offers virtual nodes v2 and the ACI Confidential SKU.
- The DC KMS issuer (`oidc_issuer_url`) is reachable with a publicly trusted certificate.

## Apply order

```bash
cd deploy/terraform/environments/tdc-azure-aks

# 1. Azure infrastructure
cd infra
cp terraform.tfvars.example terraform.tfvars          # edit
terraform init && terraform plan -out tfplan && terraform apply tfplan
terraform output                                        # values for phase 2
# Output-storage SAS broker code (spec §10.1), deployed with Entra auth
pkg="$(../../../modules/azure/federation-broker/build-package.sh)"
../../../../scripts/deploy-brokers.sh --package "$pkg" \
  --resource-group "$(terraform output -json broker_deployment | jq -r .resource_group_name)" \
  --name "$(terraform output -json broker_deployment | jq -r .function_app_name)"

# 2. In-cluster configuration (needs network access to the API server)
cd ../cluster
cp terraform.tfvars.example terraform.tfvars          # paste phase-1 outputs
terraform init && terraform plan -out tfplan && terraform apply tfplan
kubectl get nodes -l type=virtual-kubelet               # virtual node Ready
```

## After apply

1. Ask the DC to add `terraform -chdir=infra output -raw output_allowed_audience`
   (the federation broker audience by default; `api://AzureADTokenExchange` with
   `federation_mode = "entra"`) to the KMS `allowed_audiences_global`.
2. Use `terraform -chdir=infra output -json output_location` as the contract
   `output.location` (spec §8.1).
3. Submit runs with the `depa` CLI using a release manifest from `ccr/releases/`
   (`platform = aci-snp`); the Job lands in namespace `depa-ccr`.

Destroy in reverse order (`cluster/` first).
