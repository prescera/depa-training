# Offline plan: providers are mocked, nothing contacts Azure. Run with `terraform test`.
mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      client_id       = "44444444-4444-4444-4444-444444444444"
      object_id       = "55555555-5555-5555-5555-555555555555"
      tenant_id       = "11111111-1111-1111-1111-111111111111"
      subscription_id = "00000000-0000-0000-0000-000000000000"
    }
  }
}
mock_provider "time" {}
mock_provider "http" {
  mock_data "http" {
    defaults = {
      response_body = "{\"ledgerTlsCertificate\": \"-----BEGIN CERTIFICATE-----\\nMIIB\\n-----END CERTIFICATE-----\\n\"}"
      status_code   = 200
    }
  }
}

variables {
  subscription_id = "00000000-0000-0000-0000-000000000000"
  tenant_id       = "11111111-1111-1111-1111-111111111111"
  location        = "centralindia"
  region_short    = "cin"
  name_prefix     = "demo1"
}

run "plan" {
  command = plan
}
