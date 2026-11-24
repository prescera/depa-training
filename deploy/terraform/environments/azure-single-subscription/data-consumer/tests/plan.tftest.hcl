# Offline plan: providers and remote state are mocked, nothing contacts Azure. Run with `terraform test`.
mock_provider "azurerm" {}

override_data {
  target = data.terraform_remote_state.kms
  values = {
    outputs = { oidc_issuer_url = "https://demo1-kms-ab12c-cin.confidential-ledger.azure.com/app/oidc" }
  }
}

variables {
  subscription_id = "00000000-0000-0000-0000-000000000000"
  location        = "centralindia"
  name_prefix     = "demo1"
  state_backend = {
    resource_group_name  = "depa-training-v2-tfstate-rg"
    storage_account_name = "tfstate0001"
    environment          = "demo"
  }
  operator_object_ids = ["33333333-3333-3333-3333-333333333333"]
  kms_jwks_json       = "{\"keys\":[{\"kty\":\"EC\",\"crv\":\"P-256\",\"kid\":\"k1\",\"x\":\"x\",\"y\":\"y\"}]}"
}

run "plan" {
  command = plan
}
