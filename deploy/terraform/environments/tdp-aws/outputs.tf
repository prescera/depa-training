output "datasets" {
  description = "Per dataset: `location` and `federation` fragments for the CS Dataset Descriptor (spec §7.2)."
  value       = module.tdp_storage.datasets
}

output "allowed_audience" {
  description = "Add to each dataset's KMS Release Policy `allowed_audiences` (spec §7.1)."
  value       = module.tdp_storage.allowed_audience
}

output "bucket_name" {
  description = "Upload target for the encrypted dataset objects."
  value       = module.tdp_storage.bucket_name
}

output "oidc_provider_arn" {
  description = "IAM OIDC provider ARN (reuse via existing_oidc_provider_arn in other stacks of this account, e.g. TDC output storage)."
  value       = module.tdp_storage.oidc_provider_arn
}
