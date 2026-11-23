locals {
  labels = merge(var.labels, {
    workload   = "depa-training"
    component  = "ccr-confidential-space"
    managed_by = "terraform"
  })
  apis = [
    "compute.googleapis.com",
    "confidentialcomputing.googleapis.com",
    "artifactregistry.googleapis.com",
    "dns.googleapis.com",
    "iam.googleapis.com",
    "logging.googleapis.com",
  ]
  private_google_access_range = "199.36.153.8/30"
  private_google_access_ips   = ["199.36.153.8", "199.36.153.9", "199.36.153.10", "199.36.153.11"]
  private_dns_domains         = var.private_google_access_dns ? toset(["googleapis.com.", "pkg.dev.", "gcr.io."]) : toset([])
}

resource "google_project_service" "this" {
  for_each = var.enable_apis ? toset(local.apis) : toset([])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# --- Workload identity of the clean-room VM --------------------------------

resource "google_service_account" "workload" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-ccr"
  display_name = "DEPA clean-room workload (Confidential Space)"
  description  = "Attached to Confidential Space VMs running DEPA CCR releases. Holds no data access: storage access is federated from the DC KMS issuer per dataset."

  depends_on = [google_project_service.this]
}

# Required to obtain Google Cloud Attestation tokens from the Confidential Space launcher.
resource "google_project_iam_member" "workload_attestation" {
  project = var.project_id
  role    = "roles/confidentialcomputing.workloadUser"
  member  = google_service_account.workload.member
}

resource "google_project_iam_member" "workload_log_writer" {
  count = var.allow_debug_log_redirect ? 1 : 0

  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = google_service_account.workload.member
}

resource "google_artifact_registry_repository_iam_member" "workload_reader" {
  for_each = { for r in var.artifact_registry_repositories : "${r.project}/${r.location}/${r.repository}" => r }

  project    = each.value.project
  location   = each.value.location
  repository = each.value.repository
  role       = "roles/artifactregistry.reader"
  member     = google_service_account.workload.member
}

# --- Job submitters --------------------------------------------------------

resource "google_project_iam_custom_role" "job_submitter" {
  project     = var.project_id
  role_id     = replace("${var.name_prefix}_ccr_job_submitter", "-", "_")
  title       = "DEPA CCR job submitter (${var.name_prefix})"
  description = "Create, inspect and delete Confidential Space clean-room VMs; nothing else."
  permissions = concat([
    "compute.disks.create",
    "compute.instanceTemplates.get",
    "compute.instanceTemplates.useReadOnly",
    "compute.instances.create",
    "compute.instances.delete",
    "compute.instances.get",
    "compute.instances.getSerialPortOutput",
    "compute.instances.list",
    "compute.instances.setLabels",
    "compute.instances.setMetadata",
    "compute.instances.setServiceAccount",
    "compute.subnetworks.use",
    "compute.zoneOperations.get",
    "compute.zones.list",
  ], var.allow_external_ip ? ["compute.subnetworks.useExternalIp"] : [])
}

resource "google_project_iam_member" "job_submitter" {
  for_each = toset(var.job_submitter_members)

  project = var.project_id
  role    = google_project_iam_custom_role.job_submitter.id
  member  = each.value
}

# Submitters may attach exactly this service account to the VMs they create.
resource "google_service_account_iam_member" "job_submitter_sa_user" {
  for_each = toset(var.job_submitter_members)

  service_account_id = google_service_account.workload.name
  role               = "roles/iam.serviceAccountUser"
  member             = each.value
}
