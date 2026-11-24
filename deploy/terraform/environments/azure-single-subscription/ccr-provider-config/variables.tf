variable "subscription_id" {
  type        = string
  description = "The single Azure subscription hosting the whole deployment."
}

variable "name_prefix" {
  type        = string
  description = "Short prefix for every resource of this deployment (2-8 lowercase alphanumerics), identical in every component root."

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,7}$", var.name_prefix))
    error_message = "name_prefix must be 2-8 lowercase letters/digits, starting with a letter."
  }
}

variable "cluster" {
  type = object({
    resource_group_name     = string
    cluster_name            = string
    aci_resource_group_name = string
    aci_subnet_name         = string
    acr_pull_identity = optional(object({
      id           = string
      principal_id = string
    }))
  })
  description = "Outputs of the ccr-provider-cluster component (the workflows pass them with deploy/scripts/state-outputs.sh)."
}

variable "job_submitter_object_ids" {
  type        = list(string)
  description = "Principals bound to the namespaced clean-room job-submitter Role."
  default     = []
}

variable "max_concurrent_jobs" {
  type        = number
  description = "Concurrent clean-room Jobs allowed in the namespace."
  default     = 4
}

variable "kubelogin_login_mode" {
  type        = string
  description = "kubelogin --login mode (azurecli locally and in the workflows after azure/login)."
  default     = "azurecli"
}

variable "tags" {
  type        = map(string)
  description = "Extra tags."
  default     = {}
}
