terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.12"
    }
  }

  # Remote state (recommended). Create the state bucket out of band, then
  # uncomment and run `terraform init -migrate-state`.
  # backend "gcs" {
  #   bucket = "<tdp>-terraform-state"
  #   prefix = "depa-training/tdp-gcp"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
