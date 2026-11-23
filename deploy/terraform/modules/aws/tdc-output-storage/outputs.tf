output "bucket_name" {
  description = "Name of the S3 output bucket."
  value       = module.storage.bucket_name
}

output "oidc_provider_arn" {
  description = "IAM OIDC provider ARN for the KMS issuer."
  value       = module.storage.oidc_provider_arn
}

output "allowed_audience" {
  description = "Audience that must be in the KMS `allowed_audiences_global` (spec §4 set_oidc_settings) for output tokens."
  value       = module.storage.audience
}

output "role_arn" {
  description = "IAM role CCRs assume (write-only) with a KMS token whose sub = output:<party_id>."
  value       = module.storage.grants[local.subject].role_arn
}

output "output_location" {
  description = "Contract `output.location` object (spec §8.1)."
  value = {
    connector  = "s3"
    uri        = module.storage.grants[local.subject].uri
    region     = module.storage.region
    federation = { aws = { role_arn = module.storage.grants[local.subject].role_arn } }
  }
}
