output "ccr_namespace" {
  description = "Namespace clean-room Jobs are submitted to (depa CLI --namespace)."
  value       = kubernetes_namespace_v1.ccr.metadata[0].name
}

output "virtual_node_release" {
  description = "Deployed virtual node chart name and version."
  value       = "${helm_release.virtual_node.chart}-${helm_release.virtual_node.version}"
}

output "job_submitter_role" {
  description = "Name of the namespaced Role granted to job submitters."
  value       = kubernetes_role_v1.job_submitter.metadata[0].name
}
