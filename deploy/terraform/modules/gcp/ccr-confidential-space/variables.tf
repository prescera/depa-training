variable "project_id" {
  type        = string
  description = "Project that runs clean rooms. A dedicated project is strongly recommended: the job-submitter role is project-scoped."
}

variable "region" {
  type        = string
  description = "Region of the subnet and Cloud NAT. Pick one with C3 (Intel TDX) capacity, e.g. asia-south1, us-central1, europe-west4."
}

variable "name_prefix" {
  type        = string
  description = "Prefix for resource names."

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,18}[a-z0-9]$", var.name_prefix))
    error_message = "name_prefix must be 3-20 chars of lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "labels" {
  type        = map(string)
  description = "Extra labels for labelled resources."
  default     = {}
}

variable "enable_apis" {
  type        = bool
  description = "Enable the required project APIs (compute, confidentialcomputing, artifactregistry, dns, iam, logging)."
  default     = true
}

variable "subnet_cidr" {
  type        = string
  description = "Primary range of the clean-room subnet."
  default     = "10.30.0.0/24"

  validation {
    condition     = can(cidrhost(var.subnet_cidr, 0))
    error_message = "subnet_cidr must be a valid CIDR."
  }
}

variable "egress_https_destination_ranges" {
  type        = list(string)
  description = <<-EOT
    CIDRs clean rooms may reach on TCP 443 besides Google APIs: the KMS/CS endpoints (ACL ledger
    IPs and/or the DC Application Gateway IPs) and cross-cloud storage/STS ranges (AWS S3/STS,
    Azure Storage/Entra ID) for the datasets in use. Firewalls cannot match FQDNs.
  EOT
  default     = []

  validation {
    condition     = alltrue([for c in var.egress_https_destination_ranges : can(cidrhost(c, 0))])
    error_message = "egress_https_destination_ranges must contain valid CIDRs."
  }
}

variable "private_google_access_dns" {
  type        = bool
  description = "Resolve googleapis.com, pkg.dev and gcr.io to private.googleapis.com (199.36.153.8/30) inside the VPC so Google APIs (attestation, Artifact Registry, GCS, STS) are allowed without opening 0.0.0.0/0."
  default     = true
}

variable "artifact_registry_repositories" {
  type = list(object({
    project    = string
    location   = string
    repository = string
  }))
  description = "Artifact Registry repositories holding CCR images; the workload service account gets roles/artifactregistry.reader on each. Empty for public registries."
  default     = []
}

variable "job_submitter_members" {
  type        = list(string)
  description = "IAM members (user:, group:, serviceAccount:, principal://, principalSet://) allowed to create and delete clean-room VMs."
  default     = []

  validation {
    condition     = alltrue([for m in var.job_submitter_members : can(regex("^(user|group|serviceAccount|principal|principalSet):.+", m))])
    error_message = "job_submitter_members must be IAM member strings."
  }
}

variable "allow_debug_log_redirect" {
  type        = bool
  description = "Grant roles/logging.logWriter to the workload so debug releases (confidential-space-debug image, tee-container-log-redirect=true) can ship container logs. Production releases never redirect logs."
  default     = false
}

variable "allow_external_ip" {
  type        = bool
  description = "Allow submitters to attach external IPs to clean-room VMs. Default false: VMs egress via Cloud NAT so the firewall allowlist is the only path out."
  default     = false
}

variable "nat_log_filter" {
  type        = string
  description = "Cloud NAT logging filter."
  default     = "ERRORS_ONLY"

  validation {
    condition     = contains(["ERRORS_ONLY", "TRANSLATIONS_ONLY", "ALL"], var.nat_log_filter)
    error_message = "nat_log_filter must be ERRORS_ONLY, TRANSLATIONS_ONLY or ALL."
  }
}
