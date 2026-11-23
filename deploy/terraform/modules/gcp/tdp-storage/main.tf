module "storage" {
  source = "../federated-storage"

  component                 = "tdp-storage"
  workload_identity_pool_id = coalesce(var.workload_identity_pool_id, "${var.name_prefix}-tdp")
  grants = {
    for id, d in var.datasets : "dataset:${id}" => {
      access = "read"
      prefix = coalesce(d.prefix, "${id}/")
    }
  }

  project_id                         = var.project_id
  project_number                     = var.project_number
  bucket_name                        = var.bucket_name
  location                           = var.location
  storage_class                      = var.storage_class
  kms_key_name                       = var.kms_key_name
  retention_period_seconds           = var.retention_period_seconds
  soft_delete_retention_seconds      = var.soft_delete_retention_seconds
  noncurrent_version_expiration_days = var.noncurrent_version_expiration_days
  force_destroy                      = var.force_destroy
  labels                             = var.labels
  oidc_issuer_url                    = var.oidc_issuer_url
  workload_identity_provider_id      = var.workload_identity_provider_id
  allowed_audiences                  = var.allowed_audiences
}
