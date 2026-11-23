data "google_project" "this" {
  count      = var.project_number == null ? 1 : 0
  project_id = var.project_id
}

locals {
  labels = merge(var.labels, {
    workload   = "depa-training"
    component  = var.component
    managed_by = "terraform"
  })

  project_number = coalesce(var.project_number, try(data.google_project.this[0].number, null))

  principal_prefix = "principal://iam.googleapis.com/projects/${local.project_number}/locations/global/workloadIdentityPools/${google_iam_workload_identity_pool.kms.workload_identity_pool_id}/subject"

  # Only the subjects this module grants are accepted by the provider at all.
  attribute_condition = "assertion.sub in [${join(", ", [for s in sort(keys(var.grants)) : "'${s}'"])}]"

  roles = {
    read  = "roles/storage.objectViewer"
    write = "roles/storage.objectCreator"
  }
}

resource "google_storage_bucket" "this" {
  project                     = var.project_id
  name                        = var.bucket_name
  location                    = var.location
  storage_class               = var.storage_class
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = var.force_destroy
  labels                      = local.labels

  versioning {
    enabled = true
  }

  soft_delete_policy {
    retention_duration_seconds = var.soft_delete_retention_seconds
  }

  dynamic "encryption" {
    for_each = var.kms_key_name == null ? [] : [var.kms_key_name]
    content {
      default_kms_key_name = encryption.value
    }
  }

  dynamic "retention_policy" {
    for_each = var.retention_period_seconds == null ? [] : [var.retention_period_seconds]
    content {
      retention_period = retention_policy.value
    }
  }

  lifecycle_rule {
    action {
      type = "AbortIncompleteMultipartUpload"
    }
    condition {
      age = 7
    }
  }

  dynamic "lifecycle_rule" {
    for_each = var.noncurrent_version_expiration_days == null ? [] : [var.noncurrent_version_expiration_days]
    content {
      action {
        type = "Delete"
      }
      condition {
        days_since_noncurrent_time = lifecycle_rule.value
        with_state                 = "ARCHIVED"
      }
    }
  }
}
