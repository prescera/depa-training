resource "azurerm_virtual_network" "this" {
  name                = "${var.name_prefix}-ccr-vnet"
  location            = var.location
  resource_group_name = local.rg_name
  address_space       = var.vnet_address_space
  tags                = local.tags
}

resource "azurerm_subnet" "aks" {
  name                 = "aks"
  resource_group_name  = local.rg_name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [var.aks_subnet_prefix]
}

resource "azurerm_subnet" "aci" {
  name                 = "cg"
  resource_group_name  = local.rg_name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [var.aci_subnet_prefix]

  delegation {
    name = "aci"
    service_delegation {
      name    = "Microsoft.ContainerInstance/containerGroups"
      actions = ["Microsoft.Network/virtualNetworks/subnets/action"]
    }
  }
}

# Defence in depth for clean-room container groups. The CCR is the security boundary (the
# operator is untrusted and controls this NSG); these rules only reduce accidental exposure.
resource "azurerm_network_security_group" "aci" {
  name                = "${var.name_prefix}-ccr-cg-nsg"
  location            = var.location
  resource_group_name = local.rg_name
  tags                = local.tags
}

resource "azurerm_network_security_rule" "aci_https_service_tags" {
  for_each = { for i, tag in var.egress_https_service_tags : tag => i }

  name                        = "allow-https-${replace(lower(each.key), ".", "-")}"
  priority                    = 100 + each.value
  direction                   = "Outbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "443"
  source_address_prefix       = "VirtualNetwork"
  destination_address_prefix  = each.key
  resource_group_name         = local.rg_name
  network_security_group_name = azurerm_network_security_group.aci.name
}

resource "azurerm_network_security_rule" "aci_https_cidrs" {
  count = length(var.egress_https_destination_cidrs) > 0 ? 1 : 0

  name                         = "allow-https-depa-endpoints"
  priority                     = 200
  direction                    = "Outbound"
  access                       = "Allow"
  protocol                     = "Tcp"
  source_port_range            = "*"
  destination_port_range       = "443"
  source_address_prefix        = "VirtualNetwork"
  destination_address_prefixes = var.egress_https_destination_cidrs
  resource_group_name          = local.rg_name
  network_security_group_name  = azurerm_network_security_group.aci.name
}

resource "azurerm_network_security_rule" "aci_dns" {
  count = length(var.dns_server_ips) > 0 ? 1 : 0

  name                         = "allow-dns"
  priority                     = 300
  direction                    = "Outbound"
  access                       = "Allow"
  protocol                     = "*"
  source_port_range            = "*"
  destination_port_range       = "53"
  source_address_prefix        = "VirtualNetwork"
  destination_address_prefixes = var.dns_server_ips
  resource_group_name          = local.rg_name
  network_security_group_name  = azurerm_network_security_group.aci.name
}

resource "azurerm_network_security_rule" "aci_deny_outbound" {
  name                        = "deny-all-outbound"
  priority                    = 4000
  direction                   = "Outbound"
  access                      = "Deny"
  protocol                    = "*"
  source_port_range           = "*"
  destination_port_range      = "*"
  source_address_prefix       = "*"
  destination_address_prefix  = "*"
  resource_group_name         = local.rg_name
  network_security_group_name = azurerm_network_security_group.aci.name
}

# A clean room serves nothing: deny all inbound, including the default VNet/load-balancer allows.
resource "azurerm_network_security_rule" "aci_deny_inbound" {
  name                        = "deny-all-inbound"
  priority                    = 4000
  direction                   = "Inbound"
  access                      = "Deny"
  protocol                    = "*"
  source_port_range           = "*"
  destination_port_range      = "*"
  source_address_prefix       = "*"
  destination_address_prefix  = "*"
  resource_group_name         = local.rg_name
  network_security_group_name = azurerm_network_security_group.aci.name
}

resource "azurerm_subnet_network_security_group_association" "aci" {
  subnet_id                 = azurerm_subnet.aci.id
  network_security_group_id = azurerm_network_security_group.aci.id
}
