module "tdp_storage" {
  source = "../../modules/aws/tdp-storage"

  name_prefix                = var.name_prefix
  bucket_name                = var.bucket_name
  oidc_issuer_url            = var.oidc_issuer_url
  datasets                   = var.datasets
  create_oidc_provider       = var.create_oidc_provider
  existing_oidc_provider_arn = var.existing_oidc_provider_arn
  kms_key_arn                = var.kms_key_arn
  access_log_bucket          = var.access_log_bucket
  tags                       = { party = var.party_id }
}
