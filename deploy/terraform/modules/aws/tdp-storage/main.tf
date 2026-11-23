module "storage" {
  source = "../federated-storage"

  component = "tdp-storage"
  grants = {
    for id, d in var.datasets : "dataset:${id}" => {
      access = "read"
      prefix = coalesce(d.prefix, "${id}/")
    }
  }

  name_prefix                        = var.name_prefix
  bucket_name                        = var.bucket_name
  oidc_issuer_url                    = var.oidc_issuer_url
  create_oidc_provider               = var.create_oidc_provider
  existing_oidc_provider_arn         = var.existing_oidc_provider_arn
  oidc_thumbprints                   = var.oidc_thumbprints
  kms_key_arn                        = var.kms_key_arn
  access_log_bucket                  = var.access_log_bucket
  access_log_prefix                  = var.access_log_prefix
  noncurrent_version_expiration_days = var.noncurrent_version_expiration_days
  max_session_duration               = var.max_session_duration
  permissions_boundary_arn           = var.permissions_boundary_arn
  force_destroy                      = var.force_destroy
  tags                               = var.tags
}
