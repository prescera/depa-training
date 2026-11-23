locals {
  issuer = coalesce(var.kms_oidc_issuer_url, "${trimsuffix(var.platform.kms.ledger_endpoint, "/")}/app/oidc")
  suffix = var.platform.suffix
  tags = merge(var.tags, {
    workload   = "depa-training"
    deployment = "azure-pilot-${var.platform.name_prefix}"
    managed_by = "terraform"
  })
  # Short, globally unique name stems per party (storage accounts: <= 24 lowercase alphanumerics).
  short = { for p in var.parties : p.party_id => substr(replace(p.party_id, "-", ""), 0, 12) }
  tdps  = { for p in var.parties : p.party_id => p if contains(p.roles, "tdp") && length(p.datasets) > 0 }
  tdcs  = { for p in var.parties : p.party_id => p if contains(p.roles, "tdc") }
}

# --- Each TDP: encrypted dataset storage + its own SAS broker (spec §10.1) ----------------------
module "tdp_storage" {
  source   = "../../../modules/azure/tdp-storage"
  for_each = local.tdps

  name_prefix          = "d${substr(local.short[each.key], 0, 10)}-${local.suffix}"
  resource_group_name  = var.platform.party_resource_groups[each.key]
  location             = var.platform.location
  storage_account_name = "${local.short[each.key]}${local.suffix}d"
  oidc_issuer_url      = local.issuer
  datasets             = each.value.datasets
  broker = {
    package_path               = var.broker_package_path
    kms_jwks_json              = var.kms_jwks_json
    host_storage_account_name  = "${local.short[each.key]}${local.suffix}f"
    log_analytics_workspace_id = var.platform.log_analytics_workspace_id
  }
  tags = merge(local.tags, { party = each.key })
}

# --- Each TDC: write-only output storage + its own SAS broker ----------------------------------
module "tdc_output_storage" {
  source   = "../../../modules/azure/tdc-output-storage"
  for_each = local.tdcs

  party_id             = each.key
  name_prefix          = "o${substr(local.short[each.key], 0, 10)}-${local.suffix}"
  resource_group_name  = var.platform.party_resource_groups[each.key]
  location             = var.platform.location
  storage_account_name = "${local.short[each.key]}${local.suffix}o"
  oidc_issuer_url      = local.issuer
  broker = {
    package_path               = var.broker_package_path
    kms_jwks_json              = var.kms_jwks_json
    host_storage_account_name  = "${local.short[each.key]}${local.suffix}g"
    log_analytics_workspace_id = var.platform.log_analytics_workspace_id
  }
  tags = merge(local.tags, { party = each.key })
}

# --- Pilot operator access (the workflows act for every party) --------------------------------
resource "azurerm_role_assignment" "operator_dataset_upload" {
  for_each = { for pair in setproduct(keys(local.tdps), var.operator_object_ids) : "${pair[0]}/${pair[1]}" => pair }

  scope                = module.tdp_storage[each.value[0]].storage_account_id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = each.value[1]
}

resource "azurerm_role_assignment" "operator_output_read" {
  for_each = { for pair in setproduct(keys(local.tdcs), var.operator_object_ids) : "${pair[0]}/${pair[1]}" => pair }

  scope                = module.tdc_output_storage[each.value[0]].storage_account_id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = each.value[1]
}

# --- Clean-room cluster configuration (virtual nodes v2, namespace, quota, submitter RBAC) -------
module "cluster" {
  source = "../../../modules/azure/ccr-aks-vn2/cluster"
  count  = var.platform.compute == null ? 0 : 1

  aci_subnet_name         = var.platform.compute.aci_subnet_name
  aci_resource_group_name = var.platform.compute.aci_resource_group_name
  acr_pull_identity       = var.platform.compute.acr_pull_identity
  job_submitters          = [for id in var.job_submitter_object_ids : { object_id = id, kind = "User" }]
  max_concurrent_jobs     = var.max_concurrent_jobs
  custom_tags             = { deployment = "azure-pilot-${var.platform.name_prefix}" }
}
