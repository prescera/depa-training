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

variable "state_backend" {
  type = object({
    resource_group_name  = string
    storage_account_name = string
    container_name       = optional(string, "tfstate")
    environment          = string
  })
  description = "Where the other component roots keep their state (read with terraform_remote_state): depa-training/<environment>/<component>.tfstate in this storage account."
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
