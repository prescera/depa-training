# Offline plan: providers are mocked, nothing contacts Azure. Run with `terraform test`.
mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id = "11111111-1111-1111-1111-111111111111"
      object_id = "55555555-5555-5555-5555-555555555555"
    }
  }
}


variables {
  registry_id                = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/demo1-registry-rg/providers/Microsoft.ContainerRegistry/registries/demo1ab12cacr"
  subscription_id            = "00000000-0000-0000-0000-000000000000"
  location                   = "centralindia"
  name_prefix                = "demo1"
  aks_admin_group_object_ids = ["22222222-2222-2222-2222-222222222222"]
  job_submitter_object_ids   = ["33333333-3333-3333-3333-333333333333"]
}

run "plan" {
  command = plan
}
