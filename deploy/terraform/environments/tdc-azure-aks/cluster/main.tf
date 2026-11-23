module "cluster" {
  source = "../../../modules/azure/ccr-aks-vn2/cluster"

  aci_subnet_name         = var.aci_subnet_name
  aci_resource_group_name = var.aci_resource_group_name
  acr_pull_identity       = var.acr_pull_identity
  job_submitters          = var.job_submitters
  max_concurrent_jobs     = var.max_concurrent_jobs
  custom_tags             = var.custom_tags
}
