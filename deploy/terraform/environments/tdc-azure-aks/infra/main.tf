module "compute" {
  source = "../../../modules/azure/ccr-aks-vn2"

  name_prefix                     = var.name_prefix
  location                        = var.location
  admin_group_object_ids          = var.admin_group_object_ids
  job_submitters                  = var.job_submitters
  api_server_authorized_ip_ranges = var.api_server_authorized_ip_ranges
  egress_https_destination_cidrs  = var.egress_https_destination_cidrs
  acr_id                          = var.acr_id
  enable_acr_pull                 = var.acr_id != null
  create_log_analytics_workspace  = var.create_log_analytics_workspace
  tags                            = merge(var.tags, { party = var.party_id })
}

# Encrypted training outputs: clean rooms write with a KMS token whose sub = output:<party_id>.
module "output_storage" {
  source = "../../../modules/azure/tdc-output-storage"

  party_id             = var.party_id
  name_prefix          = var.name_prefix
  resource_group_name  = module.compute.resource_group_name
  location             = var.location
  storage_account_name = var.output_storage_account_name
  oidc_issuer_url      = var.oidc_issuer_url
  allowed_ip_rules     = var.output_allowed_ip_rules
  federation_mode      = var.federation_mode
  broker = var.federation_mode == "broker" ? {
    kms_jwks_json             = var.kms_jwks_json
    host_storage_account_name = var.broker_host_storage_account_name
  } : null
  tags = merge(var.tags, { party = var.party_id })
}
