output "bucket_name" {
  description = "Name of the S3 bucket."
  value       = aws_s3_bucket.this.id
}

output "bucket_arn" {
  description = "ARN of the S3 bucket."
  value       = aws_s3_bucket.this.arn
}

output "region" {
  description = "AWS region of the bucket."
  value       = data.aws_region.current.region
}

output "oidc_provider_arn" {
  description = "ARN of the IAM OIDC provider trusted by the roles (created here or passed in)."
  value       = local.oidc_provider_arn
}

output "audience" {
  description = "Token audience the roles accept; must be listed in the dataset Release Policy `allowed_audiences` / KMS `allowed_audiences_global`."
  value       = local.sts_audience
}

output "grants" {
  description = "Per OIDC subject: IAM role and the s3:// URI it is confined to."
  value = {
    for subject, g in var.grants : subject => {
      access    = g.access
      prefix    = g.prefix
      uri       = "s3://${var.bucket_name}/${g.prefix}"
      role_arn  = aws_iam_role.grant[subject].arn
      role_name = aws_iam_role.grant[subject].name
    }
  }
}
