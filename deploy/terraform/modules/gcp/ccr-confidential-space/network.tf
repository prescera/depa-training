resource "google_compute_network" "this" {
  project                         = var.project_id
  name                            = "${var.name_prefix}-ccr-vpc"
  auto_create_subnetworks         = false
  routing_mode                    = "REGIONAL"
  delete_default_routes_on_create = false

  depends_on = [google_project_service.this]
}

resource "google_compute_subnetwork" "this" {
  project                  = var.project_id
  name                     = "${var.name_prefix}-ccr-${var.region}"
  region                   = var.region
  network                  = google_compute_network.this.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true

  log_config {
    aggregation_interval = "INTERVAL_5_MIN"
    flow_sampling        = 0.5
    metadata             = "INCLUDE_ALL_METADATA"
  }
}

resource "google_compute_router" "this" {
  project = var.project_id
  name    = "${var.name_prefix}-ccr-router"
  region  = var.region
  network = google_compute_network.this.id
}

resource "google_compute_router_nat" "this" {
  project                            = var.project_id
  name                               = "${var.name_prefix}-ccr-nat"
  router                             = google_compute_router.this.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"

  subnetwork {
    name                    = google_compute_subnetwork.this.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }

  log_config {
    enable = true
    filter = var.nat_log_filter
  }
}

# Egress allowlist (defence in depth; the operator controls these rules and the CCR does not
# rely on them). Rules target the workload service account, i.e. clean-room VMs only. The
# metadata server (169.254.169.254: DNS, launcher token endpoint plumbing) is never filtered.
resource "google_compute_firewall" "deny_all_egress" {
  project                 = var.project_id
  name                    = "${var.name_prefix}-ccr-deny-egress"
  network                 = google_compute_network.this.id
  direction               = "EGRESS"
  priority                = 65000
  destination_ranges      = ["0.0.0.0/0"]
  target_service_accounts = [google_service_account.workload.email]

  deny {
    protocol = "all"
  }

  log_config {
    metadata = "INCLUDE_ALL_METADATA"
  }
}

resource "google_compute_firewall" "allow_google_apis" {
  count = var.private_google_access_dns ? 1 : 0

  project                 = var.project_id
  name                    = "${var.name_prefix}-ccr-allow-google-apis"
  network                 = google_compute_network.this.id
  direction               = "EGRESS"
  priority                = 1000
  destination_ranges      = [local.private_google_access_range]
  target_service_accounts = [google_service_account.workload.email]

  allow {
    protocol = "tcp"
    ports    = ["443"]
  }
}

resource "google_compute_firewall" "allow_https_endpoints" {
  count = length(var.egress_https_destination_ranges) > 0 ? 1 : 0

  project                 = var.project_id
  name                    = "${var.name_prefix}-ccr-allow-https"
  network                 = google_compute_network.this.id
  direction               = "EGRESS"
  priority                = 1000
  destination_ranges      = var.egress_https_destination_ranges
  target_service_accounts = [google_service_account.workload.email]

  allow {
    protocol = "tcp"
    ports    = ["443"]
  }
}

# Private Google Access DNS: *.googleapis.com / *.pkg.dev / *.gcr.io -> private.googleapis.com.
resource "google_dns_managed_zone" "private_google" {
  for_each = local.private_dns_domains

  project     = var.project_id
  name        = "${var.name_prefix}-ccr-${replace(trimsuffix(each.value, "."), ".", "-")}"
  dns_name    = each.value
  visibility  = "private"
  description = "Private Google Access for DEPA clean rooms"
  labels      = local.labels

  private_visibility_config {
    networks {
      network_url = google_compute_network.this.id
    }
  }

  depends_on = [google_project_service.this]
}

resource "google_dns_record_set" "private_google_a" {
  for_each = local.private_dns_domains

  project      = var.project_id
  managed_zone = google_dns_managed_zone.private_google[each.value].name
  name         = each.value == "googleapis.com." ? "private.googleapis.com." : each.value
  type         = "A"
  ttl          = 300
  rrdatas      = local.private_google_access_ips
}

resource "google_dns_record_set" "private_google_cname" {
  for_each = local.private_dns_domains

  project      = var.project_id
  managed_zone = google_dns_managed_zone.private_google[each.value].name
  name         = "*.${each.value}"
  type         = "CNAME"
  ttl          = 300
  rrdatas      = [each.value == "googleapis.com." ? "private.googleapis.com." : each.value]
}
