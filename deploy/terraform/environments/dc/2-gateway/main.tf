# Module sources are literals tracking `main`; pin a release tag for production (see README.md).
module "kms_gateway" {
  source = "git::https://github.com/prescera/depa-training-kms-azure.git//deploy/terraform/stacks/2-gateway?ref=main"

  core                              = var.kms_core
  ledger_private_link_service_alias = var.kms_ledger_private_link_service_alias
  runner_network                    = var.runner_network
  log_analytics_workspace_id        = var.log_analytics_workspace_id
  alert_email_addresses             = var.alert_email_addresses
  additional_action_group_ids       = var.additional_action_group_ids
  tags                              = var.tags
}

module "contract_service_gateway" {
  source = "git::https://github.com/prescera/contract-service.git//deploy/terraform/stacks/2-gateway?ref=main"
  count  = var.cs_core == null ? 0 : 1

  core                              = var.cs_core
  ledger_private_link_service_alias = var.cs_ledger_private_link_service_alias
  runner_network                    = var.runner_network
  log_analytics_workspace_id        = var.log_analytics_workspace_id
  alert_email_addresses             = var.alert_email_addresses
  additional_action_group_ids       = var.additional_action_group_ids
  tags                              = var.tags
}
