# Module sources are literals (Terraform does not allow variables in `source`) tracking `main`;
# pin a release tag for production. See README.md for local checkouts (local_override.tf).
module "kms" {
  source = "git::https://github.com/prescera/depa-training-kms-azure.git//deploy/terraform/stacks/1-core?ref=main"

  environment                        = var.environment
  region                             = var.region
  region_short                       = var.region_short
  tenant_id                          = var.tenant_id
  gateway_hostname                   = var.kms_gateway_hostname
  key_vault_allowed_ip_ranges        = var.key_vault_allowed_ip_ranges
  ledger_administrator_principal_ids = var.ledger_administrator_principal_ids
  ops_github_oidc_subjects           = var.kms_ops_github_oidc_subjects
  tags                               = var.tags
}

module "contract_service" {
  source = "git::https://github.com/prescera/contract-service.git//deploy/terraform/stacks/1-core?ref=main"

  environment                        = var.environment
  region                             = var.region
  region_short                       = var.region_short
  tenant_id                          = var.tenant_id
  enable_gateway                     = var.cs_enable_gateway
  gateway_hostname                   = var.cs_gateway_hostname
  key_vault_allowed_ip_ranges        = var.key_vault_allowed_ip_ranges
  ledger_administrator_principal_ids = var.ledger_administrator_principal_ids
  ops_github_oidc_subjects           = var.cs_ops_github_oidc_subjects
  tags                               = var.tags
}
