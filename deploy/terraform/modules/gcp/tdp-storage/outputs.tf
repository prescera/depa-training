output "bucket_name" {
  description = "Name of the GCS bucket holding the encrypted datasets."
  value       = module.storage.bucket_name
}

output "workload_identity_provider_name" {
  description = "Full resource name of the KMS OIDC provider."
  value       = module.storage.workload_identity_provider_name
}

output "allowed_audience" {
  description = "Audience to put in each dataset's Release Policy `allowed_audiences` (spec §7.1)."
  value       = module.storage.audience
}

output "datasets" {
  description = "Per dataset_id: the `location` and `federation` fragments of the CS Dataset Descriptor (spec §7.2), plus the federated principal."
  value = {
    for id, d in var.datasets : id => {
      location = {
        connector = "gcs"
        uri       = module.storage.grants["dataset:${id}"].uri
        region    = lower(module.storage.bucket_location)
      }
      federation = {
        gcp = { audience = module.storage.audience }
      }
      principal = module.storage.grants["dataset:${id}"].principal
    }
  }
}
