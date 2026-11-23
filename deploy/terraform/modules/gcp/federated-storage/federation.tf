# Trust in the DC KMS OIDC issuer (spec §10, GCP): direct resource access by
# federated principals (no service-account impersonation).

resource "google_iam_workload_identity_pool" "kms" {
  project                   = var.project_id
  workload_identity_pool_id = var.workload_identity_pool_id
  display_name              = substr("DEPA KMS ${var.component}", 0, 32)
  description               = "Tokens issued by the DEPA DC KMS (${var.oidc_issuer_url}) for CCR storage access."
}

resource "google_iam_workload_identity_pool_provider" "kms" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.kms.workload_identity_pool_id
  workload_identity_pool_provider_id = var.workload_identity_provider_id
  display_name                       = "DEPA KMS"
  description                        = "DEPA DC KMS OIDC issuer"

  attribute_mapping = {
    "google.subject" = "assertion.sub"
  }
  attribute_condition = local.attribute_condition

  oidc {
    issuer_uri        = var.oidc_issuer_url
    allowed_audiences = var.allowed_audiences
  }

  lifecycle {
    precondition {
      condition     = length(local.attribute_condition) <= 4096
      error_message = "Too many subjects for one provider attribute_condition (4096 chars); split datasets across module instances."
    }
  }
}

resource "google_storage_bucket_iam_member" "grant" {
  for_each = var.grants

  bucket = google_storage_bucket.this.name
  role   = local.roles[each.value.access]
  member = "${local.principal_prefix}/${each.key}"

  dynamic "condition" {
    for_each = each.value.prefix == "" ? [] : [each.value.prefix]
    content {
      title       = "depa-${each.value.access}-${substr(sha256(each.key), 0, 12)}"
      description = "Confine ${each.key} to gs://${google_storage_bucket.this.name}/${condition.value}"
      expression  = "resource.name.startsWith(\"projects/_/buckets/${google_storage_bucket.this.name}/objects/${condition.value}\")"
    }
  }
}
