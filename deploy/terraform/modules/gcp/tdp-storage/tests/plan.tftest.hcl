# Offline: providers are mocked, nothing contacts GCP. Run with `terraform test`.
mock_provider "google" {}

variables {
  name_prefix     = "bank-a"
  project_id      = "bank-a-data-prod"
  project_number  = "123456789012"
  bucket_name     = "bank-a-datasets"
  location        = "asia-south1"
  oidc_issuer_url = "https://kms.example.org/app/oidc"
  datasets = {
    "bank-a/credit-applications" = {}
  }
}

run "dataset_descriptor_fragments" {
  command = plan

  assert {
    condition     = output.datasets["bank-a/credit-applications"].location.uri == "gs://bank-a-datasets/bank-a/credit-applications/"
    error_message = "Default dataset prefix must be <dataset_id>/."
  }

  assert {
    condition     = output.datasets["bank-a/credit-applications"].principal == "principal://iam.googleapis.com/projects/123456789012/locations/global/workloadIdentityPools/bank-a-tdp/subject/dataset:bank-a/credit-applications"
    error_message = "Grant must target the per-dataset federated principal."
  }
}

run "rejects_dataset_id_over_google_subject_limit" {
  command = plan

  variables {
    datasets = { "bank-a/${join("", [for i in range(120) : "x"])}" = {} }
  }

  expect_failures = [var.datasets]
}
