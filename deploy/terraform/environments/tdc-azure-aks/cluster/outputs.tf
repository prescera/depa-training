output "ccr_namespace" {
  description = "Namespace for clean-room Jobs."
  value       = module.cluster.ccr_namespace
}

output "virtual_node_release" {
  description = "Deployed virtual node chart."
  value       = module.cluster.virtual_node_release
}
