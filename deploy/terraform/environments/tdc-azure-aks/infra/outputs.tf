output "cluster_name" {
  description = "AKS cluster name (cluster/ phase input)."
  value       = module.compute.cluster_name
}

output "resource_group_name" {
  description = "Main resource group (cluster/ phase input)."
  value       = module.compute.resource_group_name
}

output "aci_resource_group_name" {
  description = "Container-group resource group (cluster/ phase input)."
  value       = module.compute.aci_resource_group_name
}

output "aci_subnet_name" {
  description = "Delegated subnet name (cluster/ phase input)."
  value       = module.compute.aci_subnet_name
}

output "acr_pull_identity" {
  description = "ACR pull identity (cluster/ phase input), or null."
  value       = module.compute.acr_pull_identity
}

output "output_location" {
  description = "Contract output.location object (spec §8.1)."
  value       = module.output_storage.output_location
}

output "output_allowed_audience" {
  description = "Audience to include in the KMS allowed_audiences_global (set_oidc_settings)."
  value       = module.output_storage.audience
}

output "broker_deployment" {
  description = "Output-storage broker function app: deploy/scripts/deploy-brokers.sh --resource-group <resource_group_name> --name <function_app_name>."
  value       = module.output_storage.broker_deployment
}
