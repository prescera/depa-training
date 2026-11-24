# Offline plan: providers are mocked, nothing contacts Azure. Run with `terraform test`.
mock_provider "azurerm" {}


variables {
  kms_oidc_issuer_url = "https://demo1-kms-ab12c-cin.confidential-ledger.azure.com/app/oidc"
  subscription_id     = "00000000-0000-0000-0000-000000000000"
  location            = "centralindia"
  name_prefix         = "demo1"
  operator_object_ids = ["33333333-3333-3333-3333-333333333333"]
  kms_jwks_json       = "{\"keys\":[{\"kty\":\"EC\",\"crv\":\"P-256\",\"kid\":\"k1\",\"x\":\"x\",\"y\":\"y\"}]}"
}

run "plan" {
  command = plan
}
