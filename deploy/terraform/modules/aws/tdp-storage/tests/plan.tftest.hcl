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
  name_prefix     = "bank-a"
  bucket_name     = "bank-a-datasets"
  oidc_issuer_url = "https://kms.example.org/app/oidc"
  datasets = {
    "bank-a/credit-applications" = {}
    "bank-a/bureau"              = { prefix = "bureau/v1/" }
  }
}

run "dataset_descriptor_fragments" {
  command = plan

  assert {
    condition     = output.datasets["bank-a/credit-applications"].location.uri == "s3://bank-a-datasets/bank-a/credit-applications/"
    error_message = "Default dataset prefix must be <dataset_id>/."
  }

  assert {
    condition     = output.datasets["bank-a/bureau"].location.uri == "s3://bank-a-datasets/bureau/v1/"
    error_message = "Explicit prefix must be honoured."
  }

  assert {
    condition     = output.allowed_audience == "sts.amazonaws.com"
    error_message = "AWS federation audience must be sts.amazonaws.com."
  }

  assert {
    condition     = alltrue([for d in values(output.datasets) : length(d.role_name) <= 64])
    error_message = "IAM role names must fit 64 characters."
  }
}

run "rejects_invalid_dataset_id" {
  command = plan

  variables {
    datasets = { "Bank-A/Loans" = {} }
  }

  expect_failures = [var.datasets]
}

run "requires_existing_provider_when_not_creating" {
  command = plan

  variables {
    create_oidc_provider = false
  }

  expect_failures = [var.existing_oidc_provider_arn]
}
