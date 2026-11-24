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

# Missing state (component not deployed yet) yields the defaults below.
data "terraform_remote_state" "registry" {
  backend = "azurerm"
  config = {
    subscription_id      = var.subscription_id
    resource_group_name  = var.state_backend.resource_group_name
    storage_account_name = var.state_backend.storage_account_name
    container_name       = var.state_backend.container_name
    key                  = "depa-training/${var.state_backend.environment}/registry.tfstate"
    use_azuread_auth     = true
  }
  defaults = { id = null }
}

locals {
  acr_id = data.terraform_remote_state.registry.outputs.id
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
  acr_id                          = local.acr_id
  enable_acr_pull                 = local.acr_id != null
  create_log_analytics_workspace  = true
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
