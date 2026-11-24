output "compute" {
  description = "Clean-room submission target for render-env.sh (compute.aks)."
  value = {
    subscription_id = var.subscription_id
    resource_group  = local.cluster.resource_group_name
    cluster_name    = local.cluster.cluster_name
    namespace       = module.config.ccr_namespace
  }
}
