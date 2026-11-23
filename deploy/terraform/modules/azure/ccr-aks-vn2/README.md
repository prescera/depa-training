# azure/ccr-aks-vn2

Compute plane for `aci-snp` clean rooms: an AKS cluster whose clean-room Jobs run on
**virtual nodes v2** as **confidential Azure Container Instances** (AMD SEV-SNP). The
operator of this cluster is untrusted (spec §1): everything here is availability and
defence in depth; confidentiality comes from the CCE policy measured into `HOST_DATA`.

Two layers, applied as two Terraform phases (a Kubernetes provider must not be configured
from a cluster created in the same apply):

| Path | Provisions |
|---|---|
| `.` (this module) | Optional main RG; dedicated RG for clean-room container groups; VNet with `aks` subnet and `cg` subnet delegated to `Microsoft.ContainerInstance/containerGroups`; NSG on `cg` with an HTTPS egress allowlist (service tags + CIDRs), optional DNS, deny-all otherwise; AKS (Entra ID + Azure RBAC, local accounts disabled, OIDC issuer + workload identity, Azure CNI + Calico, AzureLinux system pool, patch auto-upgrade); user-assigned control-plane and kubelet identities with scoped grants; optional ACR pull identity; optional Log Analytics + AKS audit diagnostics; *Cluster User Role* for job submitters. |
| [`cluster/`](cluster) | Virtual nodes v2 Helm release (chart `1.3307.26033004`, images `main_20260330.4` — the DEPA-inferencing production pins); `depa-ccr` namespace (Pod Security `baseline` enforced); ResourceQuota (jobs, pods, CPU, memory, no secrets/services); `ccr-job-submitter` Role (jobs CRUD, read pods/events/logs) bound to the submitters. |

## Least privilege

- Control-plane identity: *Managed Identity Operator* on the kubelet identity, *Network Contributor* on the `aks` subnet.
- Kubelet identity (used by virtual nodes to create container groups): *Contributor* on the dedicated `<prefix>-ccr-aci-rg` only, *Network Contributor* on the `cg` subnet, *Managed Identity Operator* on the ACR pull identity (if any).
- Job submitters: *Azure Kubernetes Service Cluster User Role* (kubeconfig) + the namespaced Role. They cannot exec into pods, read secrets or create bare pods.

## Key inputs

`name_prefix`, `location`, `admin_group_object_ids` (required), `job_submitters`,
`egress_https_destination_cidrs` (KMS/CS/cross-cloud storage IPs), `egress_https_service_tags`,
`api_server_authorized_ip_ranges`, `acr_id`, `create_log_analytics_workspace` /
`log_analytics_workspace_id`. `cluster/`: `aci_subnet_name`, `aci_resource_group_name`,
`acr_pull_identity`, `job_submitters`, `max_concurrent_jobs`.

## Outputs

`cluster_name`, `cluster_id`, `cluster_host`/`cluster_ca_certificate` (sensitive),
`aci_resource_group_name`, `aci_subnet_name`, `acr_pull_identity`, `aci_nsg_id`;
`cluster/`: `ccr_namespace`, `job_submitter_role`.

## Clean-room pod contract

The SDK job submitter (`depa.deploy.aks`, template `aks_vn2_job.yaml`) renders the Job; it keeps:
one container `ccr`, `command: ["depa-ccr","run"]`, `workingDir: /depa`, DEPA_* env only, no
volumes, `automountServiceAccountToken: false`, `enableServiceLinks: false`,
`dnsPolicy: None` (168.63.129.16), toleration `virtual-kubelet.io/provider`, annotations
`microsoft.containerinstance.virtualnode.ccepolicy: <release cce_policy_b64>`,
`...injectkubeproxy: "false"`, `...injectdns: "false"`, and `activeDeadlineSeconds` ≤ the
contract's `max_duration_minutes` (spec §12). Any deviation changes nothing security-wise —
the container group simply fails policy enforcement or key release.

## Notes

- Region support: virtual nodes v2 **and** the ACI Confidential SKU must both be available
  in `location`; check before choosing.
- NSGs cannot match FQDNs. KMS/CS on ACL have no service tag; supply their IPs (or the DC
  Application Gateway IPs) via `egress_https_destination_cidrs`.
- Clean-room container groups have no volumes; the CCR keeps decrypted data in its own
  memory-backed filesystem, so size `resources.memory_gb` in the release for the dataset.
