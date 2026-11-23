variable "subscription_id" {
  type        = string
  description = "The single Azure subscription hosting the whole pilot collaborative."
}

variable "tenant_id" {
  type        = string
  description = "Entra tenant of the subscription."
}

variable "location" {
  type        = string
  description = "Region for every resource. Must offer Azure Confidential Ledger, AKS virtual nodes v2 with confidential ACI, and Functions Flex Consumption."
}

variable "region_short" {
  type        = string
  description = "Short region code used in DC service names (2-6 lowercase alphanumerics, e.g. cin)."

  validation {
    condition     = can(regex("^[a-z0-9]{2,6}$", var.region_short))
    error_message = "region_short must be 2-6 lowercase letters or digits."
  }
}

variable "name_prefix" {
  type        = string
  description = "Short prefix for every resource of this pilot (2-8 lowercase alphanumerics). A random suffix keeps globally scoped names (ledgers, storage, registry, functions) unique."

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,7}$", var.name_prefix))
    error_message = "name_prefix must be 2-8 lowercase letters/digits, starting with a letter."
  }
}

variable "parties" {
  type = list(object({
    party_id = string
    roles    = list(string)
    datasets = optional(map(object({ container_name = optional(string) })), {})
    compute  = optional(bool, false)
  }))
  description = <<-EOT
    Parties of the collaborative; each gets its own resource group. roles ⊆ [tdp, tdc, dc-member].
    `datasets` (TDPs) are keyed by dataset_id "<party_id>/<name>"; `compute = true` on exactly
    one TDC places the AKS + virtual nodes clean-room cluster in that party's group.
  EOT

  validation {
    condition     = length(var.parties) > 0 && length(distinct([for p in var.parties : p.party_id])) == length(var.parties)
    error_message = "parties must be non-empty with unique party_id values."
  }

  validation {
    condition     = alltrue([for p in var.parties : can(regex("^[a-z0-9][a-z0-9-]{1,30}$", p.party_id))])
    error_message = "party_id must match ^[a-z0-9][a-z0-9-]{1,30}$ (spec §3.1, shortened for Azure names)."
  }

  validation {
    condition     = alltrue([for p in var.parties : length(p.roles) > 0 && alltrue([for r in p.roles : contains(["tdp", "tdc", "dc-member"], r)])])
    error_message = "roles must be a non-empty subset of tdp, tdc, dc-member."
  }

  validation {
    condition     = alltrue(flatten([for p in var.parties : [for id in keys(p.datasets) : startswith(id, "${p.party_id}/") && contains(p.roles, "tdp")]]))
    error_message = "Datasets belong to TDP parties and their ids must start with \"<party_id>/\"."
  }

  validation {
    condition     = length([for p in var.parties : p if p.compute]) <= 1 && alltrue([for p in var.parties : !p.compute || contains(p.roles, "tdc")])
    error_message = "At most one party hosts compute, and it must be a TDC."
  }
}

variable "aks_admin_group_object_ids" {
  type        = list(string)
  description = "Entra groups with cluster-admin on the clean-room cluster (local accounts are disabled). Include a group containing the deployment principal."
  default     = []
}

variable "job_submitter_object_ids" {
  type        = list(string)
  description = "Object IDs of principals (e.g. the GitHub deployment service principal) allowed to submit clean-room Jobs."
  default     = []
}

variable "registry_pusher_object_ids" {
  type        = list(string)
  description = "Object IDs allowed to push CCR images to the pilot registry (AcrPush), e.g. the GitHub deployment service principal."
  default     = []
}

variable "ledger_administrator_principal_ids" {
  type        = list(string)
  description = "ACL Administrators of both ledgers (deploy bundles, bootstrap). Empty = the identity running Terraform."
  default     = []
}

variable "operator_ip_ranges" {
  type        = list(string)
  description = "Operator/runner CIDRs allowed through the DC Key Vault firewalls (Terraform creates the ledger member certificate through the data plane). The deploy workflow adds the runner's egress IP."
  default     = []
}

variable "aks_api_authorized_ip_ranges" {
  type        = list(string)
  description = "CIDRs allowed to reach the clean-room AKS API server. Empty = unrestricted (Entra ID authentication still applies), which GitHub-hosted runners need."
  default     = []
}

variable "kms_gateway_hostname" {
  type        = string
  description = "Public DNS name for an optional KMS gateway (environments/dc/2-gateway). Not used by the pilot itself: Azure storage brokers pin the KMS JWKS, so no publicly trusted issuer endpoint is needed."
  default     = null
}

variable "tags" {
  type        = map(string)
  description = "Extra tags."
  default     = {}
}
