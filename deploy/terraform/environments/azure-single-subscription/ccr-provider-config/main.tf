# In-cluster configuration of the clean-room provider: virtual nodes v2 Helm release (confidential
# ACI), the clean-room namespace, quota and job-submitter RBAC. Needs ccr-provider-cluster.
module "naming" {
  source = "../modules/naming"

  subscription_id = var.subscription_id
  name_prefix     = var.name_prefix
  tags            = var.tags
}


locals {
  cluster = var.cluster
}

data "azurerm_kubernetes_cluster" "this" {
  name                = local.cluster.cluster_name
  resource_group_name = local.cluster.resource_group_name
}

module "config" {
  source = "../../../modules/azure/ccr-aks-vn2/cluster"

  aci_subnet_name         = local.cluster.aci_subnet_name
  aci_resource_group_name = local.cluster.aci_resource_group_name
  acr_pull_identity       = local.cluster.acr_pull_identity
  job_submitters          = [for id in var.job_submitter_object_ids : { object_id = id, kind = "User" }]
  max_concurrent_jobs     = var.max_concurrent_jobs
  custom_tags             = { deployment = module.naming.tags.deployment }
}
