variable "platform" {
  type = object({
    subscription_id            = string
    tenant_id                  = string
    location                   = string
    name_prefix                = string
    suffix                     = string
    party_resource_groups      = map(string)
    log_analytics_workspace_id = string
    kms = object({
      ledger_name               = string
      ledger_endpoint           = string
      identity_service_endpoint = string
      resource_group_name       = string
    })
    cs = object({
      ledger_name               = string
      ledger_endpoint           = string
      identity_service_endpoint = string
      resource_group_name       = string
    })
    registry = object({
      id           = string
      login_server = string
    })
    compute = object({
      party_id                = string
      resource_group_name     = string
      cluster_name            = string
      aci_resource_group_name = string
      aci_subnet_name         = string
      acr_pull_identity = object({
        id           = string
        principal_id = string
      })
    })
  })
  description = "Output `pilot_platform` of 1-platform (terraform output -json pilot_platform)."
}

variable "parties" {
  type = list(object({
    party_id = string
    roles    = list(string)
    datasets = optional(map(object({ container_name = optional(string) })), {})
    compute  = optional(bool, false)
  }))
  description = "Same parties list as 1-platform."
}

variable "kms_oidc_issuer_url" {
  type        = string
  description = "KMS OIDC issuer (set_oidc_settings.issuer_url). Default: the KMS ledger app endpoint + /app/oidc; brokers pin the JWKS, so no publicly trusted gateway is needed for Azure storage."
  default     = null
}

variable "kms_jwks_json" {
  type        = string
  description = "KMS OIDC JWKS pinned into every federation broker (deploy/scripts/fetch-kms-jwks.sh, after deploy-services and DC setup)."
}

variable "broker_package_path" {
  type        = string
  description = "Federation broker package (modules/azure/federation-broker/build-package.sh)."
}

variable "job_submitter_object_ids" {
  type        = list(string)
  description = "Principals bound to the clean-room job-submitter Role (same as 1-platform)."
  default     = []
}

variable "operator_object_ids" {
  type        = list(string)
  description = "Principals that act for the parties in the pilot workflows (e.g. the GitHub deployment service principal): Storage Blob Data Contributor on TDP data accounts (encrypted dataset upload) and Storage Blob Data Reader on TDC output accounts (encrypted model download). Pilot only; real parties use their own identities."
  default     = []
}

variable "max_concurrent_jobs" {
  type        = number
  description = "Concurrent clean-room Jobs allowed in the cluster namespace."
  default     = 4
}

variable "kubelogin_login_mode" {
  type        = string
  description = "kubelogin --login mode (azurecli locally; the workflows use azurecli after azure/login)."
  default     = "azurecli"
}

variable "tags" {
  type        = map(string)
  description = "Extra tags."
  default     = {}
}
