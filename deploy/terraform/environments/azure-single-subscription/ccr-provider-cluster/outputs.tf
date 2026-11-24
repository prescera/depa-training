output "subscription_id" {
  description = "Subscription of the cluster."
  value       = var.subscription_id
}

output "resource_group_name" {
  description = "Cluster resource group."
  value       = module.cluster.resource_group_name
}

output "cluster_name" {
  description = "AKS cluster name."
  value       = module.cluster.cluster_name
}

output "aci_resource_group_name" {
  description = "Resource group for clean-room container groups."
  value       = module.cluster.aci_resource_group_name
}

output "aci_subnet_name" {
  description = "Delegated subnet name."
  value       = module.cluster.aci_subnet_name
}

output "acr_pull_identity" {
  description = "Registry pull identity ({id, principal_id}), or null."
  value       = module.cluster.acr_pull_identity
}
