variable "name_prefix" {
  type        = string
  description = "Prefix for managed identity and role names (e.g. \"bank-a-prod\")."

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.name_prefix))
    error_message = "name_prefix must match ^[a-z][a-z0-9-]{1,20}$."
  }
}

variable "component" {
  type        = string
  description = "Value of the `component` tag (set by the wrapper module)."
  default     = "federated-storage"
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

variable "grants" {
  type = map(object({
    access         = string
    container_name = string
  }))
  description = <<-EOT
    Federated grants keyed by the OIDC `sub` claim the KMS issues (e.g. "dataset:bank-a/loans"; in
    broker mode also "dataset:bank-a/*" for every dataset of that owner).
    One private container per distinct container_name and one user-assigned identity per grant
    (federated credential subject = the key). `access` = "read" (Storage Blob Data Reader) or
    "write" (custom role: blobs/write + blobs/add/action only), assigned at container scope.
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
    condition     = alltrue([for g in values(var.grants) : can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", g.container_name)) && !strcontains(g.container_name, "--")])
    error_message = "container_name must be 3-63 chars of lowercase letters, digits and single hyphens, starting and ending with a letter or digit."
  }

  validation {
    condition     = alltrue([for s in keys(var.grants) : can(regex("^(dataset|output):", s)) && length(s) <= 600])
    error_message = "Grant subjects must start with \"dataset:\" or \"output:\" (spec §10) and be <= 600 characters."
  }

  validation {
    condition     = var.federation_mode == "broker" || alltrue([for s in keys(var.grants) : !endswith(s, "/*")])
    error_message = "Owner wildcard subjects (\"dataset:<owner>/*\") need federation_mode = \"broker\"; Entra federated credentials match exact subjects."
  }
}

variable "tags" {
  type        = map(string)
  description = "Additional tags applied to all resources."
  default     = {}
}

variable "federation_mode" {
  type        = string
  description = <<-EOT
    How CCRs obtain access with a KMS token (spec §10/§10.1):
    "broker" (default) — the TDP-operated federation broker verifies the ES256 KMS token and
    returns a container-scoped user-delegation SAS. Needed while the KMS can only sign ES256,
    which Entra federated credentials do not accept.
    "entra" — native Entra federated identity credentials (one identity per subject). Enable
    once the KMS issues RS256 tokens; clean rooms only need the updated descriptor.
  EOT
  default     = "broker"

  validation {
    condition     = contains(["broker", "entra"], var.federation_mode)
    error_message = "federation_mode must be \"broker\" or \"entra\"."
  }
}

variable "broker" {
  type = object({
    kms_jwks_json              = string
    host_storage_account_name  = string
    audience                   = optional(string)
    jwks_refresh               = optional(bool, false)
    sas_max_ttl_seconds        = optional(number, 900)
    log_analytics_workspace_id = optional(string)
  })
  description = "Federation broker settings (required when federation_mode = \"broker\"); see modules/azure/federation-broker."
  default     = null

  validation {
    condition     = var.federation_mode != "broker" || var.broker != null
    error_message = "broker settings are required when federation_mode = \"broker\"."
  }
}
