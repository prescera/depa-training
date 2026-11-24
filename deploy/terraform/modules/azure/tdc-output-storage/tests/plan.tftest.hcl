# Offline: providers are mocked, nothing contacts Azure. Run with `terraform test`.
mock_provider "azurerm" {
  mock_resource "azurerm_function_app_flex_consumption" {
    defaults = {
      id               = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.Web/sites/broker"
      default_hostname = "fintech-x-broker.azurewebsites.net"
    }
  }
  mock_resource "azurerm_storage_account" {
    defaults = {
      id                    = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/acct"
      primary_blob_endpoint = "https://acct.blob.core.windows.net/"
    }
  }
  mock_resource "azurerm_storage_container" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/acct/blobServices/default/containers/c"
    }
  }
  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mi"
      principal_id = "11111111-1111-1111-1111-111111111111"
      client_id    = "22222222-2222-2222-2222-222222222222"
      tenant_id    = "33333333-3333-3333-3333-333333333333"
    }
  }
  mock_resource "azurerm_log_analytics_workspace" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/law"
    }
  }
  mock_resource "azurerm_application_insights" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.Insights/components/ai"
    }
  }
  mock_resource "azurerm_service_plan" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.Web/serverFarms/plan"
    }
  }
  mock_resource "azurerm_role_definition" {
    defaults = {
      role_definition_resource_id = "/subscriptions/00000000-0000-0000-0000-000000000000/providers/Microsoft.Authorization/roleDefinitions/44444444-4444-4444-4444-444444444444"
    }
  }
}

variables {
  name_prefix          = "fintech-x"
  resource_group_name  = "fintech-x-depa-out-rg"
  location             = "centralindia"
  storage_account_name = "fintechxccrout01"
  oidc_issuer_url      = "https://kms.example.org/app/oidc"
  party_id             = "fintech-x"
  broker = {
    kms_jwks_json             = "{\"keys\":[{\"kty\":\"EC\",\"crv\":\"P-256\",\"kid\":\"k1\",\"x\":\"x\",\"y\":\"y\"}]}"
    host_storage_account_name = "fintechxbroker01"
  }
}

run "contract_output_location" {
  command = apply

  assert {
    condition     = output.output_location.connector == "azure-blob"
    error_message = "output_location connector must be azure-blob."
  }

  assert {
    condition     = output.output_location.federation.azure.broker_url == "https://fintech-x-broker.azurewebsites.net"
    error_message = "Output federation must point at the broker by default."
  }
}
