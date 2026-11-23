variable "subscription_id" {
  type        = string
  description = "Azure subscription id."
}

variable "location" {
  type        = string
  description = "Azure region."
}

variable "resource_group_name" {
  type        = string
  description = "Resource group created for the dataset storage and identities."
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
  description = "Prefix for managed identity names."
}

variable "storage_account_name" {
  type        = string
  description = "Globally unique storage account name (3-24 lowercase alphanumerics)."
}

variable "oidc_issuer_url" {
  type        = string
  description = "DC KMS OIDC issuer URL published by the Data Collaborative (https, no trailing slash)."
}

variable "datasets" {
  type = map(object({
    container_name = optional(string)
  }))
  description = "Datasets keyed by dataset_id (\"<party_id>/<name>\"); optional explicit container name per dataset."

  validation {
    condition     = alltrue([for id in keys(var.datasets) : startswith(id, "${var.party_id}/")])
    error_message = "Every dataset id must be owned by this TDP (start with \"<party_id>/\")."
  }
}

variable "allowed_ip_rules" {
  type        = list(string)
  description = "Optional storage IP allowlist (CCR egress IPs, operator/Terraform runner IPs). Empty = any network, Entra ID-only."
  default     = []
}

variable "tags" {
  type        = map(string)
  description = "Additional tags."
  default     = {}
}

variable "federation_mode" {
  type        = string
  description = "\"broker\" (default; spec §10.1 SAS broker for ES256 KMS tokens) or \"entra\" (native federated credentials, once the KMS issues RS256)."
  default     = "broker"
}

variable "broker_package_path" {
  type        = string
  description = "Federation broker package built by modules/azure/federation-broker/build-package.sh (broker mode)."
  default     = null
}

variable "kms_jwks_json" {
  type        = string
  description = "KMS OIDC JWKS pinned into the broker (GET <issuer>/jwks, cross-checked with the KMS GET /info)."
  default     = null
}

variable "broker_host_storage_account_name" {
  type        = string
  description = "Globally unique name for the broker's own host storage account."
  default     = null
}
