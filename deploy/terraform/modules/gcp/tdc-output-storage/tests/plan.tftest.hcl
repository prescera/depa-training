# Offline: providers are mocked, nothing contacts GCP. Run with `terraform test`.
mock_provider "google" {}

variables {
  name_prefix     = "fintech-x"
  project_id      = "fintech-x-prod"
  project_number  = "210987654321"
  bucket_name     = "fintech-x-ccr-outputs"
  location        = "asia-south1"
  oidc_issuer_url = "https://kms.example.org/app/oidc"
  party_id        = "fintech-x"
}

run "contract_output_location" {
  command = plan

  assert {
    condition     = output.output_location.connector == "gcs" && output.output_location.uri == "gs://fintech-x-ccr-outputs/"
    error_message = "output_location must point at the bucket root by default."
  }

  assert {
    condition     = output.principal == "principal://iam.googleapis.com/projects/210987654321/locations/global/workloadIdentityPools/fintech-x-out/subject/output:fintech-x"
    error_message = "Grant must target the output:<party_id> principal."
  }
}
