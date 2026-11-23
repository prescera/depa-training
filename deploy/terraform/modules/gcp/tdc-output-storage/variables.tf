variable "party_id" {
  type        = string
  description = "TDC party id (spec §3.1). Only KMS tokens with sub = \"output:<party_id>\" can create objects."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,62}$", var.party_id))
    error_message = "party_id must match ^[a-z0-9][a-z0-9-]{1,62}$ (spec §3.1)."
  }
}

variable "prefix" {
  type        = string
  description = "Object prefix CCR outputs are written under (\"\" = bucket root, otherwise must end with \"/\")."
  default     = ""

  validation {
    condition     = var.prefix == "" || can(regex("^[^/].*/$", var.prefix))
    error_message = "prefix must be empty or end with \"/\"."
  }
}

variable "name_prefix" {
  type        = string
  description = "Short prefix used to derive the default Workload Identity Pool id."

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.name_prefix))
    error_message = "name_prefix must match ^[a-z][a-z0-9-]{1,20}$."
  }
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
  description = "Workload Identity Pool id (4-32 chars). Null = \"<name_prefix>-out\". Pool ids stay reserved for 30 days after deletion."
  default     = null

  validation {
    condition     = var.workload_identity_pool_id == null || (can(regex("^[a-z0-9][a-z0-9-]{2,30}[a-z0-9]$", var.workload_identity_pool_id)) && !startswith(coalesce(var.workload_identity_pool_id, "x"), "gcp-"))
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
