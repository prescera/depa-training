variable "subscription_id" {
  description = "Azure subscription hosting the DC governance plane (KMS and Contract Service)."
  type        = string
}

variable "tenant_id" {
  description = "Microsoft Entra tenant ID."
  type        = string
}

variable "environment" {
  description = "Environment code used in names (e.g. prod, uat)."
  type        = string
}

variable "region" {
  description = "Azure region offering Azure Confidential Ledger."
  type        = string
}

variable "region_short" {
  description = "Short region code used in names (e.g. cin)."
  type        = string
}

variable "key_vault_allowed_ip_ranges" {
  description = "Operator/CI egress IPs allowed through both Key Vault firewalls (Terraform creates certificates in the data plane)."
  type        = list(string)
  default     = []
}

variable "ledger_administrator_principal_ids" {
  description = "AAD Administrators of both ledgers (DC operator identities). Empty uses the identity running Terraform."
  type        = list(string)
  default     = []
}

variable "kms_gateway_hostname" {
  description = "Public DNS name of the KMS gateway; OIDC issuer = https://<kms_gateway_hostname>/app/oidc."
  type        = string
}

variable "kms_ops_github_oidc_subjects" {
  description = "GitHub OIDC subjects allowed to act as the KMS operations identity (certificate renewal), e.g. repo:prescera/depa-training-kms-azure:environment:dc-prod."
  type        = list(string)
  default     = []
}

variable "cs_enable_gateway" {
  description = "Prepare the optional Contract Service gateway."
  type        = bool
  default     = false
}

variable "cs_gateway_hostname" {
  description = "Public DNS name of the optional Contract Service gateway."
  type        = string
  default     = null
}

variable "cs_ops_github_oidc_subjects" {
  description = "GitHub OIDC subjects for the Contract Service operations identity (gateway only)."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Additional tags for all resources."
  type        = map(string)
  default     = {}
}
