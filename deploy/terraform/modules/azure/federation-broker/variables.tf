variable "name_prefix" {
  type        = string
  description = "Prefix for the Function app, plan, identities and monitoring resources."

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}$", var.name_prefix))
    error_message = "name_prefix must match ^[a-z][a-z0-9-]{1,30}$."
  }
}

variable "component" {
  type        = string
  description = "Value of the `component` tag."
  default     = "federation-broker"
}

variable "resource_group_name" {
  type        = string
  description = "Existing resource group."
}

variable "location" {
  type        = string
  description = "Azure region (must offer Flex Consumption)."
}

variable "storage_account_id" {
  type        = string
  description = "Resource ID of the data storage account the broker issues SAS for."
}

variable "storage_account_name" {
  type        = string
  description = "Name of the data storage account."
}

variable "blob_endpoint" {
  type        = string
  description = "Primary blob endpoint of the data storage account (https://<account>.blob.core.windows.net/)."

  validation {
    condition     = startswith(var.blob_endpoint, "https://")
    error_message = "blob_endpoint must be https."
  }
}

variable "grants" {
  type = map(object({
    access       = string
    container    = string
    container_id = string
    prefix       = optional(string, "")
  }))
  description = <<-EOT
    SAS grants keyed by the KMS token `sub` ("dataset:<id>" or "output:<party>"). access = "read"
    (SAS sp=rl; identity gets Storage Blob Data Reader on the container) or "write" (sp=cw;
    identity gets writer_role_definition_id on the container). container_id is the container's
    resource ID (role assignment scope).
  EOT

  validation {
    condition     = length(var.grants) > 0 && alltrue([for s, g in var.grants : contains(["read", "write"], g.access) && can(regex("^(dataset|output):", s))])
    error_message = "grants must be keyed by dataset:/output: subjects with access read or write."
  }
}

variable "writer_role_definition_id" {
  type        = string
  description = "Role definition resource ID granting blobs/write + blobs/add/action (required when any grant is write)."
  default     = null
}

variable "oidc_issuer_url" {
  type        = string
  description = "DC KMS OIDC issuer URL; tokens must carry exactly this `iss`."

  validation {
    condition     = can(regex("^https://[^/?#]+(/[^?#]*[^/?#])?$", var.oidc_issuer_url))
    error_message = "oidc_issuer_url must be an https URL without trailing slash, query or fragment."
  }
}

variable "kms_jwks_json" {
  type        = string
  description = <<-EOT
    The KMS OIDC JWKS (JSON) pinned at deployment, e.g. from `GET <issuer>/jwks` cross-checked
    against the KMS `GET /info` on the ledger endpoint (spec §10). Only ES256 P-256 keys are used.
  EOT

  validation {
    condition     = can(jsondecode(var.kms_jwks_json).keys) && length(try(jsondecode(var.kms_jwks_json).keys, [])) > 0
    error_message = "kms_jwks_json must be a JWKS document with a non-empty keys array."
  }
}

variable "jwks_refresh" {
  type        = bool
  description = "On an unknown kid, refresh the JWKS from the configured issuer's discovery document (at most every 5 minutes). false = pinned keys only; re-apply after a KMS key rotation."
  default     = false
}

variable "audience" {
  type        = string
  description = "Token audience the broker accepts (add it to the dataset Release Policy allowed_audiences / KMS allowed_audiences_global). Defaults to api://depa-federation-broker/<storage_account_name>."
  default     = null
}

variable "sas_max_ttl_seconds" {
  type        = number
  description = "Upper bound on SAS lifetime; the SAS never outlives the presented token."
  default     = 900

  validation {
    condition     = var.sas_max_ttl_seconds >= 60 && var.sas_max_ttl_seconds <= 3600
    error_message = "sas_max_ttl_seconds must be between 60 and 3600."
  }
}

variable "package_path" {
  type        = string
  description = "Path of the Function package built by build-package.sh (content-addressed file name, so a code change redeploys)."
}

variable "host_storage_account_name" {
  type        = string
  description = "Globally unique name of the Function host/deployment storage account (3-24 lowercase alphanumerics). Separate from the data account."

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.host_storage_account_name))
    error_message = "host_storage_account_name must be 3-24 lowercase letters and digits."
  }
}

variable "maximum_instance_count" {
  type        = number
  description = "Flex Consumption scale-out limit."
  default     = 40
}

variable "log_analytics_workspace_id" {
  type        = string
  description = "Existing Log Analytics workspace for the broker's Application Insights (SAS issuance audit log). null creates one."
  default     = null
}

variable "log_retention_days" {
  type        = number
  description = "Retention of the created Log Analytics workspace."
  default     = 90
}

variable "tags" {
  type        = map(string)
  description = "Extra tags."
  default     = {}
}
