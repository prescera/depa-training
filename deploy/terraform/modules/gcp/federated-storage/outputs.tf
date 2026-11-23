output "bucket_name" {
  description = "Name of the GCS bucket."
  value       = google_storage_bucket.this.name
}

output "bucket_location" {
  description = "Location of the GCS bucket."
  value       = google_storage_bucket.this.location
}

output "workload_identity_pool_name" {
  description = "Full resource name of the Workload Identity Pool."
  value       = google_iam_workload_identity_pool.kms.name
}

output "workload_identity_provider_name" {
  description = "Full resource name of the OIDC provider (projects/<number>/locations/global/workloadIdentityPools/<pool>/providers/<id>)."
  value       = google_iam_workload_identity_pool_provider.kms.name
}

output "audience" {
  description = "Token audience the provider accepts (Dataset Descriptor `federation.gcp.audience`); must be listed in the dataset Release Policy `allowed_audiences` / KMS `allowed_audiences_global`."
  value       = length(var.allowed_audiences) > 0 ? var.allowed_audiences[0] : "//iam.googleapis.com/${google_iam_workload_identity_pool_provider.kms.name}"
}

output "grants" {
  description = "Per OIDC subject: federated principal, role and the gs:// URI it is confined to."
  value = {
    for subject, g in var.grants : subject => {
      access    = g.access
      prefix    = g.prefix
      uri       = "gs://${google_storage_bucket.this.name}/${g.prefix}"
      role      = local.roles[g.access]
      principal = google_storage_bucket_iam_member.grant[subject].member
    }
  }
}
