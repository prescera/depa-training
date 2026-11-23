variable "subscription_id" {
  type        = string
  description = "Azure subscription for the compute plane and output storage."
}

variable "party_id" {
  type        = string
  description = "TDC party id as registered with the DC (spec §3.1)."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,62}$", var.party_id))
    error_message = "party_id must match ^[a-z0-9][a-z0-9-]{1,62}$."
  }
}

variable "name_prefix" {
  type        = string
  description = "Resource name prefix, e.g. \"fintechx-prod-cin\"."
}

variable "location" {
  type        = string
  description = "Region offering AKS virtual nodes v2 and confidential ACI."
}

variable "admin_group_object_ids" {
  type        = list(string)
  description = "Entra groups with cluster-admin."
}

variable "job_submitters" {
  type = list(object({
    object_id = string
    kind      = string
  }))
  description = "Entra principals allowed to submit clean-room Jobs (kind User or Group)."
  default     = []
}

variable "api_server_authorized_ip_ranges" {
  type        = list(string)
  description = "CIDRs allowed to reach the AKS API server."
  default     = []
}

variable "egress_https_destination_cidrs" {
  type        = list(string)
  description = "KMS/CS endpoint IPs and cross-cloud storage ranges clean rooms may reach on 443."
  default     = []
}

variable "acr_id" {
  type        = string
  description = "Private ACR holding CCR images, or null for public images."
  default     = null
}

variable "create_log_analytics_workspace" {
  type        = bool
  description = "Create a Log Analytics workspace for AKS audit logs."
  default     = true
}

variable "oidc_issuer_url" {
  type        = string
  description = "DC KMS OIDC issuer URL (set_oidc_settings.issuer_url)."
}

variable "output_storage_account_name" {
  type        = string
  description = "Globally unique storage account name for training outputs (3-24 lowercase alphanumerics)."
}

variable "output_allowed_ip_rules" {
  type        = list(string)
  description = "Optional IP allowlist for the output account (clean-room egress IPs + operator IPs)."
  default     = []
}

variable "tags" {
  type        = map(string)
  description = "Extra tags."
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
