variable "datasets" {
  type = map(object({
    container_name = optional(string)
  }))
  description = <<-EOT
    Datasets hosted in this account, keyed by dataset_id ("<owner_party_id>/<name>", spec §7.1), or
    "<owner_party_id>/*" (broker mode, container_name required) for one container holding every
    dataset of that owner, e.g. when dataset ids are not known in advance.
    Each dataset gets its own private container (default: the dataset name with "." and "_"
    replaced by "-") and its own user-assigned identity whose federated credential trusts
    sub = "dataset:<dataset_id>", with Storage Blob Data Reader on that container only.
  EOT

  validation {
    condition     = length(var.datasets) > 0
    error_message = "At least one dataset is required."
  }

  validation {
    condition     = alltrue([for id in keys(var.datasets) : can(regex("^[a-z0-9][a-z0-9-]{1,62}/([a-z0-9][a-z0-9._-]{0,127}|\\*)$", id))])
    error_message = "Dataset ids must match ^[a-z0-9][a-z0-9-]{1,62}/[a-z0-9][a-z0-9._-]{0,127}$ (spec §7.1), or <owner>/* for an owner wildcard."
  }

  validation {
    condition     = alltrue([for id, d in var.datasets : !endswith(id, "/*") || d.container_name != null])
    error_message = "An owner wildcard (<owner>/*) needs an explicit container_name."
  }

  validation {
    condition     = length(distinct([for id, d in var.datasets : coalesce(d.container_name, trim(substr(replace(split("/", id)[1], "/[._]+/", "-"), 0, 63), "-"))])) == length(var.datasets)
    error_message = "Each dataset must map to a distinct container (set datasets[*].container_name explicitly)."
  }
}

variable "name_prefix" {
  type        = string
  description = "Prefix for managed identity and role names (e.g. \"bank-a-prod\")."

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.name_prefix))
    error_message = "name_prefix must match ^[a-z][a-z0-9-]{1,20}$."
  }
}

variable "resource_group_name" {
  type        = string
  description = "Existing resource group for the storage account and identities."
}

variable "location" {
  type        = string
  description = "Azure region."
}

variable "storage_account_name" {
  type        = string
  description = "Globally unique storage account name (3-24 lowercase letters and digits)."

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.storage_account_name))
    error_message = "storage_account_name must be 3-24 lowercase letters and digits."
  }
}

variable "account_replication_type" {
  type        = string
  description = "Storage replication (LRS, ZRS, GRS, RAGRS, GZRS, RAGZRS)."
  default     = "ZRS"

  validation {
    condition     = contains(["LRS", "ZRS", "GRS", "RAGRS", "GZRS", "RAGZRS"], var.account_replication_type)
    error_message = "Unsupported account_replication_type."
  }
}

variable "allowed_ip_rules" {
  type        = list(string)
  description = "Optional public IPs/CIDRs allowed to reach the account (CCR egress NAT IPs, operator IPs). Empty = reachable from any network (still Entra ID-only authorization). When set, the network default action is Deny; include the Terraform runner's IP or run from an allowed network."
  default     = []

  validation {
    condition     = alltrue([for r in var.allowed_ip_rules : can(cidrhost(strcontains(r, "/") ? r : "${r}/32", 0))])
    error_message = "allowed_ip_rules entries must be IPv4 addresses or CIDRs."
  }
}

variable "blob_delete_retention_days" {
  type        = number
  description = "Soft-delete retention for blobs (days)."
  default     = 7

  validation {
    condition     = var.blob_delete_retention_days >= 1 && var.blob_delete_retention_days <= 365
    error_message = "blob_delete_retention_days must be between 1 and 365."
  }
}

variable "container_delete_retention_days" {
  type        = number
  description = "Soft-delete retention for containers (days)."
  default     = 7

  validation {
    condition     = var.container_delete_retention_days >= 1 && var.container_delete_retention_days <= 365
    error_message = "container_delete_retention_days must be between 1 and 365."
  }
}

variable "oidc_issuer_url" {
  type        = string
  description = "DC KMS OIDC issuer URL (spec §10), https and without trailing slash. Its discovery document must be reachable with a publicly trusted TLS certificate."

  validation {
    condition     = can(regex("^https://[a-zA-Z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._~%/-]*[A-Za-z0-9._~%-])?$", var.oidc_issuer_url))
    error_message = "oidc_issuer_url must be an https URL without a trailing slash, query or fragment."
  }
}

variable "tags" {
  type        = map(string)
  description = "Additional tags applied to all resources."
  default     = {}
}

variable "federation_mode" {
  type        = string
  description = "\"broker\" (default; spec §10.1 SAS broker, works with ES256 KMS tokens) or \"entra\" (native Entra federated credentials; enable once the KMS issues RS256 tokens)."
  default     = "broker"

  validation {
    condition     = contains(["broker", "entra"], var.federation_mode)
    error_message = "federation_mode must be \"broker\" or \"entra\"."
  }
}

variable "broker" {
  type = object({
    package_path               = string
    kms_jwks_json              = string
    host_storage_account_name  = string
    audience                   = optional(string)
    jwks_refresh               = optional(bool, false)
    sas_max_ttl_seconds        = optional(number, 900)
    log_analytics_workspace_id = optional(string)
  })
  description = "Federation broker settings, required in broker mode: package_path (from modules/azure/federation-broker/build-package.sh), the pinned KMS JWKS, and a globally unique host storage account name."
  default     = null

  validation {
    condition     = var.federation_mode != "broker" || var.broker != null
    error_message = "broker settings are required when federation_mode = \"broker\" (the default)."
  }
}
