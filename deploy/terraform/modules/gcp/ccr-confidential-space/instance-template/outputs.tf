output "self_link" {
  description = "Instance template self link (gcloud compute instances create --source-instance-template)."
  value       = google_compute_instance_template.this.self_link
}

output "name" {
  description = "Instance template name."
  value       = google_compute_instance_template.this.name
}
