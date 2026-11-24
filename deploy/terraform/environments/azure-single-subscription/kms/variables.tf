variable "subscription_id" {
  type        = string
  description = "The single Azure subscription hosting the whole deployment."
}

variable "tenant_id" {
  type        = string
  description = "Entra tenant of the subscription."
}

variable "location" {
  type        = string
  description = "Region for every resource (must offer Azure Confidential Ledger, AKS virtual nodes v2 with confidential pods and Functions Flex Consumption)."
}

variable "region_short" {
  type        = string
  description = "Short region code used in the ledger name (2-6 lowercase alphanumerics, e.g. cin)."

  validation {
    condition     = can(regex("^[a-z0-9]{2,6}$", var.region_short))
    error_message = "region_short must be 2-6 lowercase letters or digits."
  }
}

variable "name_prefix" {
  type        = string
  description = "Short prefix for every resource of this deployment (2-8 lowercase alphanumerics), identical in every component root."

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,7}$", var.name_prefix))
    error_message = "name_prefix must be 2-8 lowercase letters/digits, starting with a letter."
  }
}

variable "ledger_administrator_principal_ids" {
  type        = list(string)
  description = "ACL Administrators of the ledger (deploy the bundle, bootstrap). Empty = the identity running Terraform."
  default     = []
}

variable "operator_ip_ranges" {
  type        = list(string)
  description = "Operator/runner CIDRs allowed through the Key Vault firewall (the workflows add the runner's egress IP)."
  default     = []
}

variable "key_vault_network_default_action" {
  type        = string
  description = "Key Vault firewall default action. Allow: GitHub-hosted runners have changing egress IPs (data-plane access still requires Entra RBAC). Use Deny with operator_ip_ranges or a self-hosted runner subnet beyond a demo."
  default     = "Allow"

  validation {
    condition     = contains(["Allow", "Deny"], var.key_vault_network_default_action)
    error_message = "key_vault_network_default_action must be Allow or Deny."
  }
}

variable "kms_gateway_hostname" {
  type        = string
  description = "Public DNS name for an optional KMS gateway (environments/dc/2-gateway). Not used here: the Azure SAS brokers pin the KMS JWKS, so no publicly trusted issuer endpoint is needed."
  default     = null
}

variable "tags" {
  type        = map(string)
  description = "Extra tags."
  default     = {}
}
