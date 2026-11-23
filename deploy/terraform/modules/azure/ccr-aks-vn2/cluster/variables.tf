variable "aci_subnet_name" {
  type        = string
  description = "Name of the subnet delegated to container groups (output aci_subnet_name of the parent module)."
}

variable "aci_resource_group_name" {
  type        = string
  description = "Resource group for clean-room container groups (output aci_resource_group_name of the parent module)."
}

variable "zones" {
  type        = list(string)
  description = "Availability zones for container groups (empty = regional)."
  default     = []
}

variable "acr_pull_identity" {
  type = object({
    id           = string
    principal_id = string
  })
  description = "Pull identity for a private ACR (output acr_pull_identity of the parent module), or null."
  default     = null
}

variable "custom_tags" {
  type        = map(string)
  description = "Tags virtual nodes add to every container group they create."
  default     = {}
}

variable "vn2_namespace" {
  type        = string
  description = "Namespace of the virtual node control pods."
  default     = "vn2"
}

variable "vn2_chart_version" {
  type        = string
  description = "Pinned virtualnode Helm chart version (https://microsoft.github.io/virtualnodesOnAzureContainerInstances). Matches the DEPA-inferencing production pin."
  default     = "1.3307.26033004"
}

variable "vn2_image_tag" {
  type        = string
  description = "Pinned tag of the mcr.microsoft.com/aci/virtual-node-2-* images, kept in lockstep with vn2_chart_version."
  default     = "main_20260330.4"
}

variable "vn2_mount_confidential_image_tag" {
  type        = string
  description = "Pinned tag of the confidential mount-host image (lags the other images upstream)."
  default     = "main_20250801.1"
}

variable "ccr_namespace" {
  type        = string
  description = "Namespace for clean-room Jobs."
  default     = "depa-ccr"

  validation {
    condition     = can(regex("^[a-z]([-a-z0-9]{0,61}[a-z0-9])?$", var.ccr_namespace))
    error_message = "ccr_namespace must be a DNS-1123 label."
  }
}

variable "job_submitters" {
  type = list(object({
    object_id = string
    kind      = string
  }))
  description = "Entra principals bound to the job-submitter Role (same list as the parent module). kind = User (users, service principals, managed identities) or Group."
  default     = []

  validation {
    condition     = alltrue([for s in var.job_submitters : contains(["User", "Group"], s.kind)])
    error_message = "kind must be User or Group."
  }
}

variable "max_concurrent_jobs" {
  type        = number
  description = "ResourceQuota on Jobs (and their pods) in the clean-room namespace."
  default     = 4

  validation {
    condition     = var.max_concurrent_jobs >= 1 && var.max_concurrent_jobs <= 100
    error_message = "max_concurrent_jobs must be between 1 and 100."
  }
}

variable "max_namespace_cpu" {
  type        = string
  description = "ResourceQuota limits.cpu across all clean rooms in the namespace."
  default     = "32"
}

variable "max_namespace_memory" {
  type        = string
  description = "ResourceQuota limits.memory across all clean rooms in the namespace."
  default     = "128Gi"
}
