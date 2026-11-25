variable "subscription_id" {
  type        = string
  description = "The single Azure subscription hosting the whole deployment."
}

variable "location" {
  type        = string
  description = "Region for every resource (must offer Azure Confidential Ledger, AKS virtual nodes v2 with confidential pods and Functions Flex Consumption)."
}

variable "name_prefix" {
  type        = string
  description = "Short prefix for every resource of this deployment (2-8 lowercase alphanumerics), identical in every component root."

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,7}$", var.name_prefix))
    error_message = "name_prefix must be 2-8 lowercase letters/digits, starting with a letter."
  }
}

variable "registry_id" {
  type        = string
  description = "Clean-room image registry (id output of the registry component); null = no pull identity (public images). Re-apply after the registry exists."
  default     = null
}

variable "system_node_vm_size" {
  type        = string
  description = "VM size of the single system node that hosts the virtual-node control pods (clean rooms run on confidential ACI, not here). Standard_D2ds_v5 is a cheaper option to try."
  default     = "Standard_D4ds_v5"
}

variable "aks_admin_group_object_ids" {
  type        = list(string)
  description = "Entra groups with cluster-admin (local accounts are disabled). Include a group containing the deployment principal."
}

variable "job_submitter_object_ids" {
  type        = list(string)
  description = "Principals allowed to submit clean-room Jobs (Cluster User role; the namespaced Role is in ccr-provider-config)."
  default     = []
}

variable "aks_api_authorized_ip_ranges" {
  type        = list(string)
  description = "CIDRs allowed to reach the AKS API server. Empty = unrestricted (Entra ID authentication still applies), which GitHub-hosted runners need."
  default     = []
}

variable "tags" {
  type        = map(string)
  description = "Extra tags."
  default     = {}
}
