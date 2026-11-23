output "workload_service_account_email" {
  description = "Service account for clean-room VMs."
  value       = module.compute.workload_service_account_email
}

output "subnetwork_self_link" {
  description = "Subnet for clean-room VMs (create with --subnet and --no-address)."
  value       = module.compute.subnetwork_self_link
}

output "output_location" {
  description = "Contract output.location object (spec §8.1)."
  value       = module.output_storage.output_location
}

output "output_allowed_audience" {
  description = "Audience to include in the KMS allowed_audiences_global."
  value       = module.output_storage.allowed_audience
}

output "ccr_run_templates" {
  description = "Instance template self links per ccr_runs key."
  value       = { for k, m in module.ccr_run : k => m.self_link }
}
