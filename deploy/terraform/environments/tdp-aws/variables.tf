variable "region" {
  type        = string
  description = "AWS region for the dataset bucket."
}

variable "party_id" {
  type        = string
  description = "This TDP's party id as registered with the DC (spec §3.1). Every dataset id must start with \"<party_id>/\"."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,62}$", var.party_id))
    error_message = "party_id must match ^[a-z0-9][a-z0-9-]{1,62}$."
  }
}

variable "name_prefix" {
  type        = string
  description = "Prefix for IAM role names, e.g. \"<party_id>-prod\"."
}

variable "bucket_name" {
  type        = string
  description = "Globally unique S3 bucket name for the encrypted datasets."
}

variable "oidc_issuer_url" {
  type        = string
  description = "DC KMS OIDC issuer URL published by the Data Collaborative (https, no trailing slash)."
}

variable "datasets" {
  type = map(object({
    prefix = optional(string)
  }))
  description = "Datasets keyed by dataset_id (\"<party_id>/<name>\"); optional per-dataset key prefix (default \"<dataset_id>/\")."

  validation {
    condition     = alltrue([for id in keys(var.datasets) : startswith(id, "${var.party_id}/")])
    error_message = "Every dataset id must be owned by this TDP (start with \"<party_id>/\")."
  }
}

variable "create_oidc_provider" {
  type        = bool
  description = "Create the account's IAM OIDC provider for the KMS issuer. Set false if it already exists in this account."
  default     = true
}

variable "existing_oidc_provider_arn" {
  type        = string
  description = "Existing IAM OIDC provider ARN for the issuer (when create_oidc_provider = false)."
  default     = null
}

variable "kms_key_arn" {
  type        = string
  description = "Optional customer-managed KMS key ARN for SSE-KMS (default SSE-S3)."
  default     = null
}

variable "access_log_bucket" {
  type        = string
  description = "Optional existing bucket for S3 server access logs."
  default     = null
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to every resource (provider default_tags)."
  default     = {}
}
