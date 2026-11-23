variable "project_id" {
  type        = string
  description = "GCP project that hosts the dataset bucket and the Workload Identity Pool."
}

variable "region" {
  type        = string
  description = "Default region (also the bucket location unless bucket_location is set)."
}

variable "bucket_location" {
  type        = string
  description = "Bucket location; null = region."
  default     = null
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
  description = "Prefix used for the Workload Identity Pool id (<name_prefix>-tdp)."
}

variable "bucket_name" {
  type        = string
  description = "Globally unique GCS bucket name for the encrypted datasets."
}

variable "oidc_issuer_url" {
  type        = string
  description = "DC KMS OIDC issuer URL published by the Data Collaborative (https, no trailing slash)."
}

variable "datasets" {
  type = map(object({
    prefix = optional(string)
  }))
  description = "Datasets keyed by dataset_id (\"<party_id>/<name>\", <= 119 chars on GCP); optional per-dataset prefix (default \"<dataset_id>/\")."

  validation {
    condition     = alltrue([for id in keys(var.datasets) : startswith(id, "${var.party_id}/")])
    error_message = "Every dataset id must be owned by this TDP (start with \"<party_id>/\")."
  }
}

variable "kms_key_name" {
  type        = string
  description = "Optional Cloud KMS key for CMEK default bucket encryption."
  default     = null
}

variable "labels" {
  type        = map(string)
  description = "Additional labels."
  default     = {}
}
