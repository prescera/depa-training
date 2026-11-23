locals {
  labels = merge(var.labels, {
    workload   = "depa-training"
    component  = "ccr-run"
    managed_by = "terraform"
  })
  metadata = merge(
    {
      "tee-image-reference"        = var.image_reference
      "tee-restart-policy"         = "Never"
      "tee-container-log-redirect" = tostring(var.debug)
      "tee-env-DEPA_PLATFORM"      = "gcp-cs-tdx"
    },
    { for k, v in var.env : "tee-env-${k}" => v },
  )
}

# One clean-room run: a Confidential Space VM on C3 (Intel TDX) that starts the pinned CCR image
# once and is deleted when it stops or when max_run_duration elapses (spec §12 time bounds).
resource "google_compute_instance_template" "this" {
  project      = var.project_id
  name_prefix  = "${var.name_prefix}-"
  description  = "DEPA CCR run (${var.image_reference})"
  machine_type = var.machine_type
  labels       = local.labels
  metadata     = local.metadata

  can_ip_forward = false

  confidential_instance_config {
    confidential_instance_type = "TDX"
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  scheduling {
    provisioning_model          = "STANDARD"
    on_host_maintenance         = "TERMINATE"
    automatic_restart           = false
    instance_termination_action = "DELETE"

    max_run_duration {
      seconds = var.max_run_duration_seconds
    }
  }

  disk {
    boot         = true
    auto_delete  = true
    disk_type    = "pd-balanced" # C3/TDX rejects pd-standard
    disk_size_gb = var.boot_disk_size_gb
    source_image = "projects/confidential-space-images/global/images/family/${var.debug ? "confidential-space-debug" : "confidential-space"}"
  }

  # No access_config: no external IP. Egress goes through the parent module's Cloud NAT and
  # firewall allowlist.
  network_interface {
    subnetwork = var.subnetwork
  }

  service_account {
    email  = var.service_account_email
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  lifecycle {
    create_before_destroy = true
  }
}
