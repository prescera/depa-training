# Offline plan: providers are mocked, nothing contacts Azure. Run with `terraform test`.
mock_provider "azurerm" {}

variables {
  subscription_id            = "00000000-0000-0000-0000-000000000000"
  location                   = "centralindia"
  name_prefix                = "demo1"
  registry_pusher_object_ids = ["33333333-3333-3333-3333-333333333333"]
}

run "plan" {
  command = plan

  assert {
    condition     = azurerm_container_registry.this.name == "demo1${substr(sha1("00000000-0000-0000-0000-000000000000/demo1"), 0, 5)}acr"
    error_message = "Registry name must use the deterministic suffix."
  }
}
