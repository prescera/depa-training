# Offline: providers are mocked, nothing contacts AWS. Run with `terraform test`.
mock_provider "aws" {
  # aws_iam_policy_document is rendered by the provider; give the mock a valid
  # policy so aws_iam_role accepts it during plan.
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_data "aws_region" {
    defaults = { region = "ap-south-1" }
  }
}

variables {
  name_prefix                = "fintech-x"
  bucket_name                = "fintech-x-ccr-outputs"
  oidc_issuer_url            = "https://kms.example.org/app/oidc"
  party_id                   = "fintech-x"
  prefix                     = "outputs/"
  create_oidc_provider       = false
  existing_oidc_provider_arn = "arn:aws:iam::123456789012:oidc-provider/kms.example.org/app/oidc"
}

run "contract_output_location" {
  command = plan

  assert {
    condition     = output.output_location.connector == "s3" && output.output_location.uri == "s3://fintech-x-ccr-outputs/outputs/"
    error_message = "output_location must point at the output prefix."
  }

  assert {
    condition     = output.oidc_provider_arn == var.existing_oidc_provider_arn
    error_message = "An existing OIDC provider must be reused."
  }
}

run "rejects_invalid_party_id" {
  command = plan

  variables {
    party_id = "Fintech_X"
  }

  expect_failures = [var.party_id]
}
