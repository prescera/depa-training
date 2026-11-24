# Offline plan of the whole phase: providers are mocked, nothing contacts Azure.
# Catches plan-time errors that `terraform validate` cannot see, such as a count or for_each
# that depends on values only known after apply. Run with `terraform test`.
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
mock_provider "random" {}
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
  parties = [
    { party_id = "dc-member-1", roles = ["dc-member"] },
    { party_id = "tdc", roles = ["tdc", "tdp"], compute = true, datasets = { "tdc/*" = { container_name = "datasets" } } },
    { party_id = "tdp-1", roles = ["tdp"], datasets = { "tdp-1/*" = { container_name = "datasets" } } },
    { party_id = "tdp-2", roles = ["tdp"], datasets = { "tdp-2/*" = { container_name = "datasets" } } },
    { party_id = "tdp-3", roles = ["tdp"], datasets = { "tdp-3/*" = { container_name = "datasets" } } },
    { party_id = "tdp-4", roles = ["tdp"], datasets = { "tdp-4/*" = { container_name = "datasets" } } },
  ]
  aks_admin_group_object_ids = ["22222222-2222-2222-2222-222222222222"]
  job_submitter_object_ids   = ["33333333-3333-3333-3333-333333333333"]
  registry_pusher_object_ids = ["33333333-3333-3333-3333-333333333333"]
}

run "plan" {
  command = plan
}
