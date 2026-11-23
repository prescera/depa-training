output "workload_service_account_email" {
  description = "Service account to attach to clean-room VMs (depa CLI --service-account)."
  value       = google_service_account.workload.email
}

output "network_self_link" {
  description = "VPC self link (depa CLI --network)."
  value       = google_compute_network.this.self_link
}

output "subnetwork_self_link" {
  description = "Subnet self link (depa CLI --subnet; instance-template sub-module subnetwork)."
  value       = google_compute_subnetwork.this.self_link
}

output "region" {
  description = "Region of the subnet; create VMs in a zone of this region that offers C3."
  value       = var.region
}

output "job_submitter_role_id" {
  description = "Custom role granted to job submitters."
  value       = google_project_iam_custom_role.job_submitter.id
}
