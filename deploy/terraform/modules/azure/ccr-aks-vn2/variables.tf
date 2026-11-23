variable "name_prefix" {
  type        = string
  description = "Prefix for every resource name (e.g. \"fintechx-prod-cin\")."

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}[a-z0-9]$", var.name_prefix))
    error_message = "name_prefix must be 3-32 chars of lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "location" {
  type        = string
  description = "Azure region. It must offer AKS virtual nodes v2 and confidential container groups (ACI Confidential SKU)."
}

variable "create_resource_group" {
  type        = bool
  description = "Create the main resource group. Set false to deploy into an existing one named resource_group_name."
  default     = true
}

variable "resource_group_name" {
  type        = string
  description = "Main resource group name. Defaults to \"<name_prefix>-ccr-rg\"."
  default     = null
}

variable "tags" {
  type        = map(string)
  description = "Extra tags applied to every taggable resource."
  default     = {}
}

# --- Networking -----------------------------------------------------------

variable "vnet_address_space" {
  type        = list(string)
  description = "Address space of the compute VNet."
  default     = ["10.20.0.0/16"]
}

variable "aks_subnet_prefix" {
  type        = string
  description = "CIDR of the AKS system node subnet (Azure CNI: one IP per pod, size accordingly)."
  default     = "10.20.0.0/22"

  validation {
    condition     = can(cidrhost(var.aks_subnet_prefix, 0))
    error_message = "aks_subnet_prefix must be a valid CIDR."
  }
}

variable "aci_subnet_prefix" {
  type        = string
  description = "CIDR of the subnet delegated to Microsoft.ContainerInstance/containerGroups where clean rooms run (one IP per concurrent CCR)."
  default     = "10.20.8.0/22"

  validation {
    condition     = can(cidrhost(var.aci_subnet_prefix, 0))
    error_message = "aci_subnet_prefix must be a valid CIDR."
  }
}

variable "service_cidr" {
  type        = string
  description = "Kubernetes service CIDR; must not overlap the VNet or peered networks."
  default     = "10.21.0.0/16"
}

variable "dns_service_ip" {
  type        = string
  description = "Cluster DNS service IP, inside service_cidr."
  default     = "10.21.0.10"
}

variable "egress_https_service_tags" {
  type        = list(string)
  description = <<-EOT
    Azure service tags clean rooms may reach on TCP 443. Defaults cover Entra ID token exchange,
    Azure Storage (TDP/TDC blob containers), ACR and MCR image pulls. Narrow regional tags
    (e.g. "Storage.CentralIndia") are preferred where the storage region is known.
  EOT
  default = [
    "AzureActiveDirectory",
    "Storage",
    "AzureContainerRegistry",
    "MicrosoftContainerRegistry",
    "AzureFrontDoor.FirstParty",
  ]

  validation {
    condition     = alltrue([for t in var.egress_https_service_tags : can(regex("^[A-Za-z][A-Za-z0-9.]*$", t))])
    error_message = "egress_https_service_tags must be Azure service tag names."
  }
}

variable "egress_https_destination_cidrs" {
  type        = list(string)
  description = <<-EOT
    Extra CIDRs clean rooms may reach on TCP 443: the KMS/CS endpoints (ACL ledger IPs and/or the
    DC Application Gateway public IPs) and, for cross-cloud datasets, the AWS S3/STS or Google
    API ranges in use. NSGs cannot match FQDNs; keep this list in sync with the DC endpoints.
  EOT
  default     = []

  validation {
    condition     = alltrue([for c in var.egress_https_destination_cidrs : can(cidrhost(c, 0))])
    error_message = "egress_https_destination_cidrs must contain valid CIDRs."
  }
}

variable "dns_server_ips" {
  type        = list(string)
  description = "Custom DNS servers clean rooms may query on port 53. Azure DNS (168.63.129.16) is platform traffic and is never filtered by NSGs."
  default     = []

  validation {
    condition     = alltrue([for ip in var.dns_server_ips : can(cidrhost("${ip}/32", 0))])
    error_message = "dns_server_ips must be IPv4 addresses."
  }
}

# --- AKS ------------------------------------------------------------------

variable "kubernetes_version" {
  type        = string
  description = "AKS Kubernetes version (minor, e.g. \"1.33\"). null selects the region default."
  default     = null
}

variable "aks_sku_tier" {
  type        = string
  description = "AKS control-plane tier."
  default     = "Standard"

  validation {
    condition     = contains(["Free", "Standard", "Premium"], var.aks_sku_tier)
    error_message = "aks_sku_tier must be Free, Standard or Premium."
  }
}

variable "system_node_vm_size" {
  type        = string
  description = "VM size of the system pool that hosts the virtual node control pods (no clean room runs on it)."
  default     = "Standard_D4ds_v5"
}

variable "system_node_count" {
  type        = number
  description = "Number of system nodes."
  default     = 2

  validation {
    condition     = var.system_node_count >= 1 && var.system_node_count <= 10
    error_message = "system_node_count must be between 1 and 10."
  }
}

variable "availability_zones" {
  type        = list(string)
  description = "Availability zones for the system pool (empty = regional)."
  default     = []
}

variable "admin_group_object_ids" {
  type        = list(string)
  description = "Entra ID group object IDs that get cluster-admin through AKS-managed Entra integration. Local accounts are disabled."

  validation {
    condition     = length(var.admin_group_object_ids) > 0
    error_message = "At least one admin group is required because local accounts are disabled."
  }
}

variable "api_server_authorized_ip_ranges" {
  type        = list(string)
  description = "CIDRs allowed to reach the public API server (operators, CI, job submitters). Empty = unrestricted (Entra authentication still applies)."
  default     = []

  validation {
    condition     = alltrue([for c in var.api_server_authorized_ip_ranges : can(cidrhost(c, 0))])
    error_message = "api_server_authorized_ip_ranges must contain valid CIDRs."
  }
}

variable "azure_policy_enabled" {
  type        = bool
  description = "Enable the Azure Policy add-on."
  default     = true
}

variable "job_submitters" {
  type = list(object({
    object_id = string
    kind      = string
  }))
  description = <<-EOT
    Entra principals allowed to submit clean-room Jobs. kind = "User" for users, service principals
    and managed identities, "Group" for groups. They receive "Azure Kubernetes Service Cluster User
    Role" (to fetch a kubeconfig); the namespaced Kubernetes Role is created by the cluster/ module.
  EOT
  default     = []

  validation {
    condition     = alltrue([for s in var.job_submitters : contains(["User", "Group"], s.kind) && can(regex("^[0-9a-f-]{36}$", s.object_id))])
    error_message = "Each job submitter needs a GUID object_id and kind User or Group."
  }
}

# --- Optional integrations ------------------------------------------------

variable "acr_id" {
  type        = string
  description = "Resource ID of a private ACR holding the CCR images. When set, a dedicated pull identity with AcrPull is created and handed to virtual nodes (acrTrustedAccess). null for public registries."
  default     = null
}

variable "log_analytics_workspace_id" {
  type        = string
  description = "Existing Log Analytics workspace for AKS control-plane audit logs. Ignored when create_log_analytics_workspace is true. null disables diagnostics."
  default     = null
}

variable "create_log_analytics_workspace" {
  type        = bool
  description = "Create a Log Analytics workspace for AKS control-plane audit logs."
  default     = false
}

variable "log_retention_days" {
  type        = number
  description = "Retention of the created Log Analytics workspace."
  default     = 90

  validation {
    condition     = var.log_retention_days >= 30 && var.log_retention_days <= 730
    error_message = "log_retention_days must be between 30 and 730."
  }
}
