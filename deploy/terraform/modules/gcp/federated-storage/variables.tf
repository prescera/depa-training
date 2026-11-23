variable "component" {
  type        = string
  description = "Value of the `component` label (set by the wrapper module)."
  default     = "federated-storage"
}

variable "project_id" {
  type        = string
  description = "GCP project id that hosts the bucket and the Workload Identity Pool."

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a valid GCP project id."
  }
}

variable "project_number" {
  type        = string
  description = "Numeric project number (used in principal:// identifiers). Null = looked up from project_id."
  default     = null

  validation {
    condition     = var.project_number == null || can(regex("^[0-9]{6,20}$", var.project_number))
    error_message = "project_number must be numeric."
  }
}

variable "bucket_name" {
  type        = string
  description = "Globally unique GCS bucket name."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9_-]{1,61}[a-z0-9]$", var.bucket_name)) && !startswith(var.bucket_name, "goog")
    error_message = "bucket_name must be 3-63 chars of lowercase letters, digits, '-' or '_' (dots not supported here) and must not start with \"goog\"."
  }
}

variable "location" {
  type        = string
  description = "Bucket location (region such as \"asia-south1\", dual-region or multi-region)."
}

variable "storage_class" {
  type        = string
  description = "Default storage class of the bucket."
  default     = "STANDARD"

  validation {
    condition     = contains(["STANDARD", "NEARLINE", "COLDLINE", "ARCHIVE"], var.storage_class)
    error_message = "storage_class must be STANDARD, NEARLINE, COLDLINE or ARCHIVE."
  }
}

variable "kms_key_name" {
  type        = string
  description = "Optional Cloud KMS key (projects/.../cryptoKeys/...) for CMEK default encryption. The GCS service agent needs roles/cloudkms.cryptoKeyEncrypterDecrypter on it. Null = Google-managed keys (data is already client-side encrypted)."
  default     = null
}

variable "retention_period_seconds" {
  type        = number
  description = "Optional bucket retention policy (objects cannot be deleted/overwritten before this age). Null = none."
  default     = null
}

variable "soft_delete_retention_seconds" {
  type        = number
  description = "Soft-delete retention for deleted objects (0 disables, otherwise 604800-7776000)."
  default     = 604800

  validation {
    condition     = var.soft_delete_retention_seconds == 0 || (var.soft_delete_retention_seconds >= 604800 && var.soft_delete_retention_seconds <= 7776000)
    error_message = "soft_delete_retention_seconds must be 0 or between 604800 (7d) and 7776000 (90d)."
  }
}

variable "noncurrent_version_expiration_days" {
  type        = number
  description = "Delete non-current object versions this many days after they become non-current. Null keeps them."
  default     = null
}

variable "force_destroy" {
  type        = bool
  description = "Allow `terraform destroy` to delete a non-empty bucket. Keep false for real data."
  default     = false
}

variable "labels" {
  type        = map(string)
  description = "Additional labels (lowercase keys/values)."
  default     = {}
}

variable "oidc_issuer_url" {
  type        = string
  description = "DC KMS OIDC issuer URL (spec §10), https and without trailing slash. Must serve a publicly trusted TLS certificate."

  validation {
    condition     = can(regex("^https://[a-zA-Z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._~%/-]*[A-Za-z0-9._~%-])?$", var.oidc_issuer_url))
    error_message = "oidc_issuer_url must be an https URL without a trailing slash, query or fragment."
  }
}

variable "workload_identity_pool_id" {
  type        = string
  description = "Workload Identity Pool id (4-32 chars). Pool ids stay reserved for 30 days after deletion."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{2,30}[a-z0-9]$", var.workload_identity_pool_id)) && !startswith(var.workload_identity_pool_id, "gcp-")
    error_message = "workload_identity_pool_id must be 4-32 chars of [a-z0-9-] and must not start with \"gcp-\"."
  }
}

variable "workload_identity_provider_id" {
  type        = string
  description = "Workload Identity Pool provider id (4-32 chars)."
  default     = "depa-kms"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{2,30}[a-z0-9]$", var.workload_identity_provider_id)) && !startswith(var.workload_identity_provider_id, "gcp-")
    error_message = "workload_identity_provider_id must be 4-32 chars of [a-z0-9-] and must not start with \"gcp-\"."
  }
}

variable "allowed_audiences" {
  type        = list(string)
  description = "Token audiences the provider accepts. Empty = the provider's default audience (//iam.googleapis.com/<provider resource name>), which is what the Dataset Descriptor `federation.gcp.audience` carries."
  default     = []
}

variable "grants" {
  type = map(object({
    access = string
    prefix = string
  }))
  description = <<-EOT
    Federated grants keyed by the OIDC `sub` claim the KMS issues (e.g. "dataset:bank-a/loans").
    `access` = "read" (roles/storage.objectViewer) or "write" (roles/storage.objectCreator: create
    only, no overwrite/delete). `prefix` confines the binding via an IAM condition ("" = whole
    bucket, otherwise must end with "/").
  EOT

  validation {
    condition     = length(var.grants) > 0
    error_message = "At least one grant is required."
  }

  validation {
    condition     = alltrue([for g in values(var.grants) : contains(["read", "write"], g.access)])
    error_message = "grants[*].access must be \"read\" or \"write\"."
  }

  validation {
    condition     = alltrue([for g in values(var.grants) : g.prefix == "" || can(regex("^[^/].*/$", g.prefix))])
    error_message = "grants[*].prefix must be empty or end with \"/\" (no leading \"/\")."
  }

  validation {
    condition     = alltrue([for s in keys(var.grants) : can(regex("^(dataset|output):[a-z0-9][a-z0-9./_-]*$", s))])
    error_message = "Grant subjects must look like \"dataset:<dataset_id>\" or \"output:<party_id>\" (spec §10)."
  }

  validation {
    condition     = alltrue([for s in keys(var.grants) : length(s) <= 127])
    error_message = "google.subject is limited to 127 bytes: \"dataset:<dataset_id>\" must be <= 127 characters, so dataset ids hosted on GCP must be <= 119 characters."
  }
}
