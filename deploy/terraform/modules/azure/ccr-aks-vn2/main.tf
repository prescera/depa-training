locals {
  resource_group_name = coalesce(var.resource_group_name, "${var.name_prefix}-ccr-rg")
  tags = merge(var.tags, {
    workload   = "depa-training"
    component  = "ccr-aks-vn2"
    managed_by = "terraform"
  })
  # Referencing the created group (when any) orders every resource after it; an existing group
  # (possibly created by the caller in the same apply) is referenced by name only.
  rg_name                    = var.create_resource_group ? azurerm_resource_group.this[0].name : local.resource_group_name
  log_analytics_workspace_id = var.create_log_analytics_workspace ? azurerm_log_analytics_workspace.this[0].id : var.log_analytics_workspace_id
}

data "azurerm_client_config" "current" {}

resource "azurerm_resource_group" "this" {
  count = var.create_resource_group ? 1 : 0

  name     = local.resource_group_name
  location = var.location
  tags     = local.tags
}

# Container groups created by virtual nodes land in this dedicated resource group, so the
# kubelet identity's Contributor grant is confined to clean-room container groups only.
resource "azurerm_resource_group" "aci" {
  name     = "${var.name_prefix}-ccr-aci-rg"
  location = var.location
  tags     = local.tags
}

resource "azurerm_kubernetes_cluster" "this" {
  name                = "${var.name_prefix}-ccr-aks"
  location            = var.location
  resource_group_name = local.rg_name
  node_resource_group = "${var.name_prefix}-ccr-aks-nodes-rg"
  dns_prefix          = "${var.name_prefix}-ccr"
  kubernetes_version  = var.kubernetes_version
  sku_tier            = var.aks_sku_tier
  tags                = local.tags

  oidc_issuer_enabled               = true
  workload_identity_enabled         = true
  local_account_disabled            = true
  role_based_access_control_enabled = true
  azure_policy_enabled              = var.azure_policy_enabled
  automatic_upgrade_channel         = "patch"
  node_os_upgrade_channel           = "NodeImage"
  image_cleaner_enabled             = true
  image_cleaner_interval_hours      = 48

  azure_active_directory_role_based_access_control {
    azure_rbac_enabled     = true
    tenant_id              = data.azurerm_client_config.current.tenant_id
    admin_group_object_ids = var.admin_group_object_ids
  }

  dynamic "api_server_access_profile" {
    for_each = length(var.api_server_authorized_ip_ranges) > 0 ? [1] : []
    content {
      authorized_ip_ranges = var.api_server_authorized_ip_ranges
    }
  }

  default_node_pool {
    name                        = "system"
    temporary_name_for_rotation = "systemtmp"
    vm_size                     = var.system_node_vm_size
    node_count                  = var.system_node_count
    vnet_subnet_id              = azurerm_subnet.aks.id
    os_sku                      = "AzureLinux"
    zones                       = length(var.availability_zones) > 0 ? var.availability_zones : null
    tags                        = local.tags

    upgrade_settings {
      max_surge = "10%"
    }
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.control_plane.id]
  }

  kubelet_identity {
    client_id                 = azurerm_user_assigned_identity.kubelet.client_id
    object_id                 = azurerm_user_assigned_identity.kubelet.principal_id
    user_assigned_identity_id = azurerm_user_assigned_identity.kubelet.id
  }

  network_profile {
    network_plugin    = "azure"
    network_policy    = "calico"
    load_balancer_sku = "standard"
    outbound_type     = "loadBalancer"
    service_cidr      = var.service_cidr
    dns_service_ip    = var.dns_service_ip
  }

  lifecycle {
    ignore_changes = [
      # Managed by the automatic patch upgrade channel.
      kubernetes_version,
      default_node_pool[0].orchestrator_version,
    ]
  }

  depends_on = [
    azurerm_role_assignment.control_plane_kubelet_operator,
    azurerm_role_assignment.control_plane_aks_subnet,
  ]
}

resource "azurerm_log_analytics_workspace" "this" {
  count = var.create_log_analytics_workspace ? 1 : 0

  name                = "${var.name_prefix}-ccr-law"
  location            = var.location
  resource_group_name = local.rg_name
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  tags                = local.tags
}

# Control-plane audit trail: who submitted / deleted clean-room jobs, and Entra authn decisions.
resource "azurerm_monitor_diagnostic_setting" "aks" {
  count = var.create_log_analytics_workspace || var.enable_diagnostics ? 1 : 0

  lifecycle {
    precondition {
      condition     = var.create_log_analytics_workspace || var.log_analytics_workspace_id != null
      error_message = "enable_diagnostics requires log_analytics_workspace_id (or create_log_analytics_workspace)."
    }
  }

  name                           = "${var.name_prefix}-ccr-aks-audit"
  target_resource_id             = azurerm_kubernetes_cluster.this.id
  log_analytics_workspace_id     = local.log_analytics_workspace_id
  log_analytics_destination_type = "Dedicated"

  enabled_log {
    category = "kube-audit-admin"
  }

  enabled_log {
    category = "guard"
  }
}
