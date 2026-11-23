# AKS control-plane identity: joins the node subnet and assigns the kubelet identity.
resource "azurerm_user_assigned_identity" "control_plane" {
  name                = "${var.name_prefix}-ccr-aks-cp-mi"
  location            = var.location
  resource_group_name = local.rg_name
  tags                = local.tags
}

# Kubelet identity: used by virtual nodes v2 to create clean-room container groups.
resource "azurerm_user_assigned_identity" "kubelet" {
  name                = "${var.name_prefix}-ccr-aks-kubelet-mi"
  location            = var.location
  resource_group_name = local.rg_name
  tags                = local.tags
}

resource "azurerm_role_assignment" "control_plane_kubelet_operator" {
  scope                = azurerm_user_assigned_identity.kubelet.id
  role_definition_name = "Managed Identity Operator"
  principal_id         = azurerm_user_assigned_identity.control_plane.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "control_plane_aks_subnet" {
  scope                = azurerm_subnet.aks.id
  role_definition_name = "Network Contributor"
  principal_id         = azurerm_user_assigned_identity.control_plane.principal_id
  principal_type       = "ServicePrincipal"
}

# Virtual nodes v2 create/delete container groups in the dedicated ACI resource group and join
# them to the delegated subnet. Nothing broader (the reference grants RG-wide Contributor).
resource "azurerm_role_assignment" "kubelet_aci_rg" {
  scope                = azurerm_resource_group.aci.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.kubelet.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "kubelet_aci_subnet" {
  scope                = azurerm_subnet.aci.id
  role_definition_name = "Network Contributor"
  principal_id         = azurerm_user_assigned_identity.kubelet.principal_id
  principal_type       = "ServicePrincipal"
}

# Optional private-registry pull identity attached to clean-room container groups.
resource "azurerm_user_assigned_identity" "acr_pull" {
  count = var.acr_id != null ? 1 : 0

  name                = "${var.name_prefix}-ccr-acrpull-mi"
  location            = var.location
  resource_group_name = local.rg_name
  tags                = local.tags
}

resource "azurerm_role_assignment" "acr_pull" {
  count = var.acr_id != null ? 1 : 0

  scope                = var.acr_id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.acr_pull[0].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "kubelet_acr_pull_operator" {
  count = var.acr_id != null ? 1 : 0

  scope                = azurerm_user_assigned_identity.acr_pull[0].id
  role_definition_name = "Managed Identity Operator"
  principal_id         = azurerm_user_assigned_identity.kubelet.principal_id
  principal_type       = "ServicePrincipal"
}

# Job submitters only need to download a (Entra-authenticated) kubeconfig; what they may do
# inside the cluster is the namespaced Role from the cluster/ module.
resource "azurerm_role_assignment" "job_submitter_cluster_user" {
  for_each = { for s in var.job_submitters : s.object_id => s }

  scope                = azurerm_kubernetes_cluster.this.id
  role_definition_name = "Azure Kubernetes Service Cluster User Role"
  principal_id         = each.value.object_id
}
