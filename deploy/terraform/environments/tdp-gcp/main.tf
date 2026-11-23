resource "google_project_service" "required" {
  for_each = toset([
    "iam.googleapis.com",
    "sts.googleapis.com",
    "storage.googleapis.com",
  ])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

module "tdp_storage" {
  source = "../../modules/gcp/tdp-storage"

  name_prefix     = var.name_prefix
  project_id      = var.project_id
  bucket_name     = var.bucket_name
  location        = coalesce(var.bucket_location, var.region)
  oidc_issuer_url = var.oidc_issuer_url
  datasets        = var.datasets
  kms_key_name    = var.kms_key_name
  labels          = merge(var.labels, { party = var.party_id })

  depends_on = [google_project_service.required]
}
