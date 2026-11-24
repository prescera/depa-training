# The KMS of the Data Collaborative on its own Azure Confidential Ledger, with its Key Vault
# (member certificate) and network. The module source is a literal tracking `main`; the GitHub
# workflows check out <owner>/depa-training-kms-azure at DEPA_SERVICES_REF and render
# local_override.tf.tpl to use that checkout.

module "naming" {
  source = "../modules/naming"

  subscription_id = var.subscription_id
  name_prefix     = var.name_prefix
  tags            = var.tags
}

module "kms" {
  source = "git::https://github.com/prescera/depa-training-kms-azure.git//deploy/terraform/stacks/1-core?ref=main"

  name_prefix                        = "${var.name_prefix}-kms"
  environment                        = module.naming.suffix
  region                             = var.location
  region_short                       = var.region_short
  tenant_id                          = var.tenant_id
  resource_group_name                = module.naming.resource_groups.kms
  gateway_hostname                   = coalesce(var.kms_gateway_hostname, "kms.${var.name_prefix}.internal")
  key_vault_allowed_ip_ranges        = var.operator_ip_ranges
  key_vault_network_default_action   = var.key_vault_network_default_action
  ledger_administrator_principal_ids = var.ledger_administrator_principal_ids
  tags                               = merge(module.naming.tags, { party = "dc" })
}
