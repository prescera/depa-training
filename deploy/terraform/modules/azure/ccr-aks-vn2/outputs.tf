output "resource_group_name" {
  description = "Main resource group."
  value       = local.rg_name
}

output "cluster_id" {
  description = "AKS cluster resource ID."
  value       = azurerm_kubernetes_cluster.this.id
}

output "cluster_name" {
  description = "AKS cluster name (az aks get-credentials --name)."
  value       = azurerm_kubernetes_cluster.this.name
}

output "cluster_host" {
  description = "Kubernetes API server URL, for the cluster/ phase providers."
  value       = azurerm_kubernetes_cluster.this.kube_config[0].host
  sensitive   = true
}

output "cluster_ca_certificate" {
  description = "Base64 PEM CA of the API server, for the cluster/ phase providers."
  value       = azurerm_kubernetes_cluster.this.kube_config[0].cluster_ca_certificate
  sensitive   = true
}

output "oidc_issuer_url" {
  description = "AKS OIDC issuer (workload identity). Not the DEPA KMS issuer."
  value       = azurerm_kubernetes_cluster.this.oidc_issuer_url
}

output "aci_resource_group_name" {
  description = "Resource group where virtual nodes create clean-room container groups (helm value aciResourceGroupName)."
  value       = azurerm_resource_group.aci.name
}

output "aci_subnet_name" {
  description = "Delegated subnet name (helm value aciSubnetName)."
  value       = azurerm_subnet.aci.name
}

output "aci_subnet_id" {
  description = "Delegated subnet resource ID."
  value       = azurerm_subnet.aci.id
}

output "aci_nsg_id" {
  description = "NSG enforcing the clean-room egress allowlist."
  value       = azurerm_network_security_group.aci.id
}

output "vnet_id" {
  description = "Compute VNet resource ID."
  value       = azurerm_virtual_network.this.id
}

output "kubelet_identity_principal_id" {
  description = "Object ID of the kubelet identity used by virtual nodes."
  value       = azurerm_user_assigned_identity.kubelet.principal_id
}

output "acr_pull_identity" {
  description = "Pull identity for the private ACR ({id, principal_id}), or null."
  value = var.acr_id == null ? null : {
    id           = azurerm_user_assigned_identity.acr_pull[0].id
    principal_id = azurerm_user_assigned_identity.acr_pull[0].principal_id
  }
}

output "log_analytics_workspace_id" {
  description = "Workspace receiving AKS audit logs, or null."
  value       = local.log_analytics_workspace_id
}
