module "compute" {
  source = "../../modules/gcp/ccr-confidential-space"

  project_id                      = var.project_id
  region                          = var.region
  name_prefix                     = var.name_prefix
  egress_https_destination_ranges = var.egress_https_destination_ranges
  job_submitter_members           = var.job_submitter_members
  artifact_registry_repositories  = var.artifact_registry_repositories
  labels                          = merge(var.labels, { party = var.party_id })
}

# Encrypted training outputs: clean rooms write with a KMS token whose sub = output:<party_id>.
module "output_storage" {
  source = "../../modules/gcp/tdc-output-storage"

  party_id        = var.party_id
  name_prefix     = var.name_prefix
  project_id      = var.project_id
  bucket_name     = var.output_bucket_name
  location        = var.output_bucket_location
  oidc_issuer_url = var.oidc_issuer_url
  labels          = merge(var.labels, { party = var.party_id })
}

module "ccr_run" {
  source   = "../../modules/gcp/ccr-confidential-space/instance-template"
  for_each = var.ccr_runs

  project_id               = var.project_id
  name_prefix              = "${var.name_prefix}-${each.key}"
  service_account_email    = module.compute.workload_service_account_email
  subnetwork               = module.compute.subnetwork_self_link
  image_reference          = each.value.image_reference
  max_run_duration_seconds = each.value.max_run_duration_seconds
  env                      = each.value.env
  machine_type             = each.value.machine_type
  labels                   = merge(var.labels, { party = var.party_id })
}
