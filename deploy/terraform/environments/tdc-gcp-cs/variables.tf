variable "project_id" {
  type        = string
  description = "Dedicated clean-room project."
}

variable "region" {
  type        = string
  description = "Region with C3 (TDX) capacity."
}

variable "party_id" {
  type        = string
  description = "TDC party id as registered with the DC (spec §3.1)."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,62}$", var.party_id))
    error_message = "party_id must match ^[a-z0-9][a-z0-9-]{1,62}$."
  }
}

variable "name_prefix" {
  type        = string
  description = "Resource name prefix (3-20 chars)."
}

variable "egress_https_destination_ranges" {
  type        = list(string)
  description = "KMS/CS endpoint IPs and cross-cloud storage ranges clean rooms may reach on 443."
  default     = []
}

variable "job_submitter_members" {
  type        = list(string)
  description = "IAM members allowed to create/delete clean-room VMs."
  default     = []
}

variable "artifact_registry_repositories" {
  type = list(object({
    project    = string
    location   = string
    repository = string
  }))
  description = "Artifact Registry repositories with CCR images (empty for public images)."
  default     = []
}

variable "oidc_issuer_url" {
  type        = string
  description = "DC KMS OIDC issuer URL."
}

variable "output_bucket_name" {
  type        = string
  description = "Globally unique bucket for encrypted training outputs."
}

variable "output_bucket_location" {
  type        = string
  description = "GCS location of the output bucket (e.g. ASIA-SOUTH1)."
}

variable "ccr_runs" {
  type = map(object({
    image_reference          = string
    max_run_duration_seconds = number
    env                      = map(string)
    machine_type             = optional(string, "c3-standard-4")
  }))
  description = "Optional Terraform-managed run templates keyed by a short name (alternative to submitting runs with the depa CLI). Each yields an instance template; create the VM with `gcloud compute instances create --source-instance-template`."
  default     = {}
}

variable "labels" {
  type        = map(string)
  description = "Extra labels."
  default     = {}
}
