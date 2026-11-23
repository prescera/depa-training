variable "project_id" {
  type        = string
  description = "Project that runs clean rooms."
}

variable "name_prefix" {
  type        = string
  description = "Instance template name prefix (a random suffix is appended)."

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,36}$", var.name_prefix))
    error_message = "name_prefix must be 2-37 chars of lowercase letters, digits and hyphens."
  }
}

variable "labels" {
  type        = map(string)
  description = "Extra labels (lowercase keys/values) for the template and the VMs created from it."
  default     = {}
}

variable "service_account_email" {
  type        = string
  description = "Workload service account (output workload_service_account_email of the parent module)."
}

variable "subnetwork" {
  type        = string
  description = "Subnet self link (output subnetwork_self_link of the parent module)."
}

variable "machine_type" {
  type        = string
  description = "C3 machine type; Intel TDX Confidential VMs require the C3 series."
  default     = "c3-standard-4"

  validation {
    condition     = can(regex("^c3-(standard|highcpu|highmem)-[0-9]+$", var.machine_type))
    error_message = "machine_type must be a C3 machine type (TDX)."
  }
}

variable "boot_disk_size_gb" {
  type        = number
  description = "Boot disk size. The boot disk holds the Confidential Space OS and the pulled image layers."
  default     = 30
}

variable "image_reference" {
  type        = string
  description = "Digest-pinned CCR image (release manifest deployment.image), e.g. ghcr.io/prescera/depa-ccr-classic@sha256:..."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9._/:-]*@sha256:[0-9a-f]{64}$", var.image_reference))
    error_message = "image_reference must be pinned by digest (<repo>@sha256:<64 hex>)."
  }
}

variable "max_run_duration_seconds" {
  type        = number
  description = "Hard VM lifetime (seconds); the VM is deleted afterwards. Must not exceed the contract's max_duration_minutes * 60."

  validation {
    condition     = var.max_run_duration_seconds >= 60 && var.max_run_duration_seconds <= 604800
    error_message = "max_run_duration_seconds must be between 60 and 604800 (7 days)."
  }
}

variable "env" {
  type        = map(string)
  description = "CCR runtime environment (spec §12), passed as tee-env-* metadata. Allowed keys: DEPA_CONTRACT_ID, DEPA_RECIPE_DIGEST, DEPA_CS_URL, DEPA_KMS_URL, DEPA_TRUST_BUNDLE, DEPA_LOG_LEVEL (DEPA_PLATFORM is always gcp-cs-tdx). The image launch policy rejects any other override."

  validation {
    condition = alltrue([for k in keys(var.env) : contains([
      "DEPA_CONTRACT_ID", "DEPA_RECIPE_DIGEST", "DEPA_CS_URL", "DEPA_KMS_URL", "DEPA_TRUST_BUNDLE", "DEPA_LOG_LEVEL",
    ], k)])
    error_message = "env keys must be among the DEPA_* runtime variables (DEPA_PLATFORM is set by the module)."
  }

  validation {
    condition     = alltrue([for k in ["DEPA_CONTRACT_ID", "DEPA_RECIPE_DIGEST", "DEPA_CS_URL", "DEPA_KMS_URL"] : contains(keys(var.env), k)])
    error_message = "env must set DEPA_CONTRACT_ID, DEPA_RECIPE_DIGEST, DEPA_CS_URL and DEPA_KMS_URL."
  }
}

variable "debug" {
  type        = bool
  description = "Use the confidential-space-debug image and redirect container logs. Debug VMs attest dbgstat=enabled and are rejected by production releases."
  default     = false
}
