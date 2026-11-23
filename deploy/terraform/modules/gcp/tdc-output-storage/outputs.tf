output "bucket_name" {
  description = "Name of the GCS output bucket."
  value       = module.storage.bucket_name
}

output "workload_identity_provider_name" {
  description = "Full resource name of the KMS OIDC provider."
  value       = module.storage.workload_identity_provider_name
}

output "allowed_audience" {
  description = "Audience that must be in the KMS `allowed_audiences_global` (spec §4 set_oidc_settings) for output tokens."
  value       = module.storage.audience
}

output "principal" {
  description = "Federated principal (roles/storage.objectCreator) for sub = output:<party_id>."
  value       = module.storage.grants[local.subject].principal
}

output "output_location" {
  description = "Contract `output.location` object (spec §8.1)."
  value = {
    connector  = "gcs"
    uri        = module.storage.grants[local.subject].uri
    region     = lower(module.storage.bucket_location)
    federation = { gcp = { audience = module.storage.audience } }
  }
}
