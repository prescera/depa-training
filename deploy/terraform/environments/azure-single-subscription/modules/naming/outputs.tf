output "suffix" {
  description = "Deterministic 5-character suffix for globally unique names."
  value       = local.suffix
}

output "tags" {
  description = "Base tags for every resource."
  value = merge(var.tags, {
    workload   = "depa-training"
    deployment = "azure-single-subscription-${var.name_prefix}"
    managed_by = "terraform"
  })
}

output "resource_groups" {
  description = "Resource group per component / party."
  value = {
    kms              = "${var.name_prefix}-dc-kms-rg"
    contract_service = "${var.name_prefix}-dc-cs-rg"
    registry         = "${var.name_prefix}-registry-rg"
    ccr_provider     = "${var.name_prefix}-ccr-provider-rg"
    tdc              = "${var.name_prefix}-tdc-rg"
    "tdp-1"          = "${var.name_prefix}-tdp-1-rg"
    "tdp-2"          = "${var.name_prefix}-tdp-2-rg"
    "tdp-3"          = "${var.name_prefix}-tdp-3-rg"
    "tdp-4"          = "${var.name_prefix}-tdp-4-rg"
  }
}

output "registry_name" {
  description = "Container registry name (5-50 lowercase alphanumerics)."
  value       = "${var.name_prefix}${local.suffix}acr"
}

output "ccr_provider_name_prefix" {
  description = "name_prefix of the clean-room cluster module."
  value       = "${var.name_prefix}-ccr"
}
