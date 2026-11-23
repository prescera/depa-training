variable "subscription_id" {
  description = "Azure subscription hosting the DC governance plane."
  type        = string
}

variable "tenant_id" {
  description = "Microsoft Entra tenant ID."
  type        = string
}

variable "kms_core" {
  description = "`kms_gateway_stack_inputs` output of ../1-core."
  type = object({
    resource_group_name          = string
    base_name                    = string
    gateway_hostname             = string
    virtual_network_name         = string
    gateway_subnet_name          = string
    private_endpoint_subnet_name = string
    key_vault_name               = string
    frontend_certificate_name    = string
    gateway_identity_name        = string
    ops_identity_principal_id    = string
    ledger_name                  = string
  })
}

variable "kms_ledger_private_link_service_alias" {
  description = "Alias of the KMS ledger Private Link Service (manual step after phase 1)."
  type        = string
}

variable "cs_core" {
  description = "`cs_gateway_stack_inputs` output of ../1-core; null skips the optional Contract Service gateway."
  type = object({
    resource_group_name          = string
    base_name                    = string
    gateway_hostname             = string
    virtual_network_name         = string
    gateway_subnet_name          = string
    private_endpoint_subnet_name = string
    key_vault_name               = string
    frontend_certificate_name    = string
    gateway_identity_name        = string
    ops_identity_principal_id    = string
    ledger_name                  = string
  })
  default = null
}

variable "cs_ledger_private_link_service_alias" {
  description = "Alias of the Contract Service ledger Private Link Service (required with cs_core)."
  type        = string
  default     = null

  validation {
    condition     = var.cs_core == null || var.cs_ledger_private_link_service_alias != null
    error_message = "cs_ledger_private_link_service_alias is required when cs_core is set."
  }
}

variable "runner_network" {
  description = "Optional self-hosted runner subnet/VNet that gets its own private endpoints (certificate renewal, private operator access)."
  type = object({
    subnet_id          = string
    virtual_network_id = string
  })
  default = null
}

variable "log_analytics_workspace_id" {
  description = "Existing Log Analytics workspace (e.g. the DC SIEM); null creates one per service."
  type        = string
  default     = null
}

variable "alert_email_addresses" {
  description = "Alert e-mail receivers keyed by name."
  type        = map(string)
  default     = {}
}

variable "additional_action_group_ids" {
  description = "Existing action groups to notify."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Additional tags for all resources."
  type        = map(string)
  default     = {}
}
