variable "subscription_id" {
  type        = string
  description = "Azure subscription of the cluster."
}

variable "cluster_name" {
  type        = string
  description = "AKS cluster name (infra/ output cluster_name)."
}

variable "resource_group_name" {
  type        = string
  description = "Cluster resource group (infra/ output resource_group_name)."
}

variable "aci_resource_group_name" {
  type        = string
  description = "Container-group resource group (infra/ output aci_resource_group_name)."
}

variable "aci_subnet_name" {
  type        = string
  description = "Delegated subnet name (infra/ output aci_subnet_name)."
  default     = "cg"
}

variable "acr_pull_identity" {
  type = object({
    id           = string
    principal_id = string
  })
  description = "infra/ output acr_pull_identity, or null."
  default     = null
}

variable "job_submitters" {
  type = list(object({
    object_id = string
    kind      = string
  }))
  description = "Same list as infra/ job_submitters."
  default     = []
}

variable "max_concurrent_jobs" {
  type        = number
  description = "Maximum concurrent clean-room Jobs."
  default     = 4
}

variable "kubelogin_login_mode" {
  type        = string
  description = "kubelogin --login mode: azurecli for operators, workloadidentity / msi / spn in automation."
  default     = "azurecli"

  validation {
    condition     = contains(["azurecli", "workloadidentity", "msi", "spn", "devicecode", "interactive"], var.kubelogin_login_mode)
    error_message = "Unsupported kubelogin login mode."
  }
}

variable "custom_tags" {
  type        = map(string)
  description = "Tags added to every clean-room container group."
  default     = {}
}
