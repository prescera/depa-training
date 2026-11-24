# Container registry for clean-room (CCR) release images.

module "naming" {
  source = "../modules/naming"

  subscription_id = var.subscription_id
  name_prefix     = var.name_prefix
  tags            = var.tags
}

resource "azurerm_resource_group" "this" {
  name     = module.naming.resource_groups.registry
  location = var.location
  tags     = merge(module.naming.tags, { party = "dc" })
}

resource "azurerm_container_registry" "this" {
  name                          = module.naming.registry_name
  resource_group_name           = azurerm_resource_group.this.name
  location                      = var.location
  sku                           = "Standard"
  admin_enabled                 = false
  anonymous_pull_enabled        = false
  public_network_access_enabled = true
  tags                          = module.naming.tags
}

resource "azurerm_role_assignment" "acr_push" {
  for_each = toset(var.registry_pusher_object_ids)

  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPush"
  principal_id         = each.value
}
