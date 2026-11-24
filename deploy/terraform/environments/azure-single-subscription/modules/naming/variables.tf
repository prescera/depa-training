variable "subscription_id" {
  type        = string
  description = "The single Azure subscription hosting the whole deployment."
}

variable "name_prefix" {
  type        = string
  description = "Deployment name prefix."
}

variable "tags" {
  type        = map(string)
  description = "Extra tags."
  default     = {}
}
