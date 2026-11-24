# Offline plan: providers and remote state are mocked, nothing contacts Azure. Run with `terraform test`.
mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id = "11111111-1111-1111-1111-111111111111"
      object_id = "55555555-5555-5555-5555-555555555555"
    }
  }
}

override_data {
  target = data.terraform_remote_state.registry
  values = {
    outputs = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/demo1-registry-rg/providers/Microsoft.ContainerRegistry/registries/demo1ab12cacr" }
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
  aks_admin_group_object_ids = ["22222222-2222-2222-2222-222222222222"]
  job_submitter_object_ids   = ["33333333-3333-3333-3333-333333333333"]
}

run "plan" {
  command = plan
}
