# The clean-room (CCR) provider's cluster: AKS with virtual nodes v2 infrastructure (delegated
# subnet, egress NSG, identities), the registry pull identity and Log Analytics audit logs. The
# in-cluster configuration (virtual nodes release, namespace, RBAC) is ccr-provider-config, a
# separate root because its Kubernetes providers need this cluster to exist.
module "naming" {
  source = "../modules/naming"

  subscription_id = var.subscription_id
  name_prefix     = var.name_prefix
  tags            = var.tags
}



module "cluster" {
  source = "../../../modules/azure/ccr-aks-vn2"

  name_prefix                     = module.naming.ccr_provider_name_prefix
  location                        = var.location
  create_resource_group           = true
  resource_group_name             = module.naming.resource_groups.ccr_provider
  admin_group_object_ids          = var.aks_admin_group_object_ids
  job_submitters                  = [for id in var.job_submitter_object_ids : { object_id = id, kind = "User" }]
  api_server_authorized_ip_ranges = var.aks_api_authorized_ip_ranges
  acr_id                          = var.registry_id
  enable_acr_pull                 = var.registry_id != null
  create_log_analytics_workspace  = true
  # Demo sizing: Free control-plane tier and one system node (no zones); clean rooms run on
  # confidential ACI and are billed per second, so the node pool is the only always-on compute.
  aks_sku_tier = "Free"
  # Two nodes: with Azure CNI each node takes about 30 pods, and the system pods plus the virtual
  # node pods do not fit on one (the virtual node then never becomes Ready). Pause the cluster
  # between runs (Pause CCR Provider) to stop paying for the nodes.
  system_node_count   = 2
  system_node_vm_size = var.system_node_vm_size
  # Demo simplification: the KMS/CS ledgers have no service tag, so clean rooms may reach any
  # Azure endpoint in the region on 443 (defence in depth only; see the module README).
  egress_https_service_tags = [
    "AzureActiveDirectory",
    "Storage",
    "AzureContainerRegistry",
    "MicrosoftContainerRegistry",
    "AzureFrontDoor.FirstParty",
    "AzureCloud.${var.location}",
  ]
  tags = merge(module.naming.tags, { party = "ccr-provider" })
}
