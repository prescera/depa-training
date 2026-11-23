output "bucket_name" {
  description = "Name of the S3 bucket holding the encrypted datasets."
  value       = module.storage.bucket_name
}

output "oidc_provider_arn" {
  description = "IAM OIDC provider ARN for the KMS issuer (pass to other stacks in this account via existing_oidc_provider_arn)."
  value       = module.storage.oidc_provider_arn
}

output "allowed_audience" {
  description = "Audience to put in each dataset's Release Policy `allowed_audiences` (spec §7.1)."
  value       = module.storage.audience
}

output "datasets" {
  description = "Per dataset_id: the `location` and `federation` fragments of the CS Dataset Descriptor (spec §7.2), plus the role name."
  value = {
    for id, d in var.datasets : id => {
      location = {
        connector = "s3"
        uri       = module.storage.grants["dataset:${id}"].uri
        region    = module.storage.region
      }
      federation = {
        aws = { role_arn = module.storage.grants["dataset:${id}"].role_arn }
      }
      role_name = module.storage.grants["dataset:${id}"].role_name
    }
  }
}
