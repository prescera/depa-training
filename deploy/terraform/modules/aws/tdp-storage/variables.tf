variable "datasets" {
  type = map(object({
    prefix = optional(string)
  }))
  description = <<-EOT
    Datasets hosted in this bucket, keyed by dataset_id ("<owner_party_id>/<name>", spec §7.1).
    Each gets an IAM role assumable only with a KMS-issued token whose sub = "dataset:<dataset_id>",
    with read access to its prefix (default "<dataset_id>/").
  EOT

  validation {
    condition     = length(var.datasets) > 0
    error_message = "At least one dataset is required."
  }

  validation {
    condition     = alltrue([for id in keys(var.datasets) : can(regex("^[a-z0-9][a-z0-9-]{1,62}/[a-z0-9][a-z0-9._-]{0,127}$", id))])
    error_message = "Dataset ids must match ^[a-z0-9][a-z0-9-]{1,62}/[a-z0-9][a-z0-9._-]{0,127}$ (spec §7.1)."
  }

  validation {
    condition     = alltrue([for d in values(var.datasets) : d.prefix == null || can(regex("^[^/*?].*/$", d.prefix))])
    error_message = "datasets[*].prefix must be non-empty and end with \"/\"."
  }
}

variable "name_prefix" {
  type        = string
  description = "Prefix for IAM role names (e.g. \"bank-a-prod\")."

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.name_prefix))
    error_message = "name_prefix must match ^[a-z][a-z0-9-]{1,20}$."
  }
}

variable "bucket_name" {
  type        = string
  description = "Globally unique S3 bucket name."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.bucket_name)) && !strcontains(var.bucket_name, "..")
    error_message = "bucket_name must be a valid S3 bucket name (3-63 chars, lowercase letters, digits, dots, hyphens)."
  }
}

variable "oidc_issuer_url" {
  type        = string
  description = "DC KMS OIDC issuer URL (spec §10 `set_oidc_settings.issuer_url`), https and without trailing slash. Must serve a publicly trusted TLS certificate."

  validation {
    condition     = can(regex("^https://[a-zA-Z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._~%/-]*[A-Za-z0-9._~%-])?$", var.oidc_issuer_url))
    error_message = "oidc_issuer_url must be an https URL without a trailing slash, query or fragment."
  }
}

variable "create_oidc_provider" {
  type        = bool
  description = "Create the IAM OIDC identity provider for oidc_issuer_url. AWS allows one provider per issuer URL per account; set false and pass existing_oidc_provider_arn when another stack (e.g. the TDP storage in the same account) already created it."
  default     = true
}

variable "existing_oidc_provider_arn" {
  type        = string
  description = "ARN of an existing IAM OIDC provider for oidc_issuer_url. Required when create_oidc_provider = false."
  default     = null

  validation {
    condition     = var.existing_oidc_provider_arn == null || can(regex("^arn:aws[a-z-]*:iam::[0-9]{12}:oidc-provider/.+$", var.existing_oidc_provider_arn))
    error_message = "existing_oidc_provider_arn must be an IAM OIDC provider ARN."
  }

  validation {
    condition     = var.create_oidc_provider || var.existing_oidc_provider_arn != null
    error_message = "existing_oidc_provider_arn is required when create_oidc_provider = false."
  }
}

variable "oidc_thumbprints" {
  type        = list(string)
  description = "Optional SHA-1 thumbprints of the issuer's TLS CA. AWS validates publicly trusted issuers against its own CA store, so this is normally empty."
  default     = []

  validation {
    condition     = alltrue([for t in var.oidc_thumbprints : can(regex("^[0-9a-fA-F]{40}$", t))])
    error_message = "Each thumbprint must be 40 hex characters."
  }
}

variable "kms_key_arn" {
  type        = string
  description = "Optional customer-managed KMS key ARN for SSE-KMS default encryption. Null = SSE-S3 (AES256). Objects are already client-side encrypted (dedf/1); the key policy must allow IAM policies in this account to grant use."
  default     = null

  validation {
    condition     = var.kms_key_arn == null || can(regex("^arn:aws[a-z-]*:kms:[a-z0-9-]+:[0-9]{12}:key/.+$", var.kms_key_arn))
    error_message = "kms_key_arn must be a KMS key ARN."
  }
}

variable "access_log_bucket" {
  type        = string
  description = "Optional existing bucket that receives S3 server access logs. Null disables access logging."
  default     = null
}

variable "access_log_prefix" {
  type        = string
  description = "Key prefix for server access logs (used only when access_log_bucket is set)."
  default     = "s3-access/"
}

variable "noncurrent_version_expiration_days" {
  type        = number
  description = "Expire non-current object versions after this many days. Null keeps them indefinitely."
  default     = null

  validation {
    condition     = var.noncurrent_version_expiration_days == null || try(var.noncurrent_version_expiration_days >= 1, false)
    error_message = "noncurrent_version_expiration_days must be >= 1 or null."
  }
}

variable "max_session_duration" {
  type        = number
  description = "Maximum STS session duration (seconds) for the federated roles."
  default     = 3600

  validation {
    condition     = var.max_session_duration >= 3600 && var.max_session_duration <= 43200
    error_message = "max_session_duration must be between 3600 and 43200."
  }
}

variable "permissions_boundary_arn" {
  type        = string
  description = "Optional IAM permissions boundary attached to every federated role."
  default     = null
}

variable "force_destroy" {
  type        = bool
  description = "Allow `terraform destroy` to delete a non-empty bucket. Keep false for real data."
  default     = false
}

variable "tags" {
  type        = map(string)
  description = "Additional tags applied to all resources."
  default     = {}
}
