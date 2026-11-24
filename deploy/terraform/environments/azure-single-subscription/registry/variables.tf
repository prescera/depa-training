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

variable "registry_pusher_object_ids" {
  type        = list(string)
  description = "Object IDs allowed to push clean-room images (AcrPush), e.g. the GitHub deployment service principal."
  default     = []
}

variable "tags" {
  type        = map(string)
  description = "Extra tags."
  default     = {}
}
