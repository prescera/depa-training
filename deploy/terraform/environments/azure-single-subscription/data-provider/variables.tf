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

variable "state_backend" {
  type = object({
    resource_group_name  = string
    storage_account_name = string
    container_name       = optional(string, "tfstate")
    environment          = string
  })
  description = "Where the other component roots keep their state (read with terraform_remote_state): depa-training/<environment>/<component>.tfstate in this storage account."
}

variable "party_id" {
  type        = string
  description = "Data provider slot of the demo deployment (scenario providers map onto tdp-1..tdp-4). One state per provider: depa-training/<environment>/data-provider-<party_id>.tfstate."

  validation {
    condition     = can(regex("^tdp-[1-4]$", var.party_id))
    error_message = "party_id must be one of tdp-1, tdp-2, tdp-3, tdp-4."
  }
}

variable "kms_jwks_json" {
  type        = string
  description = "KMS OIDC JWKS pinned into the SAS broker(s) (deploy/scripts/fetch-kms-jwks.sh, after the KMS is bootstrapped)."
}

variable "kms_oidc_issuer_url" {
  type        = string
  description = "KMS OIDC issuer. Default: the oidc_issuer_url output of the kms component."
  default     = null
}

variable "operator_object_ids" {
  type        = list(string)
  description = "Principals acting for the parties in the demo workflows (e.g. the GitHub deployment service principal). Demo only; real parties use their own identities."
  default     = []
}

variable "tags" {
  type        = map(string)
  description = "Extra tags."
  default     = {}
}
