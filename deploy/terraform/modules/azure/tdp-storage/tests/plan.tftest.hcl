# Offline: providers are mocked, nothing contacts Azure. Run with `terraform test`.
mock_provider "azurerm" {
  mock_resource "azurerm_function_app_flex_consumption" {
    defaults = {
      id               = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.Web/sites/broker"
      default_hostname = "bank-a-broker.azurewebsites.net"
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
  name_prefix          = "bank-a"
  resource_group_name  = "bank-a-depa-data-rg"
  location             = "centralindia"
  storage_account_name = "bankadepadata01"
  oidc_issuer_url      = "https://kms.example.org/app/oidc"
  datasets = {
    "bank-a/credit-applications" = {}
    "bank-a/bureau.v2"           = {}
  }
  broker = {
    package_path              = "dist/federation-broker-000000000000.zip"
    kms_jwks_json             = "{\"keys\":[{\"kty\":\"EC\",\"crv\":\"P-256\",\"kid\":\"k1\",\"x\":\"x\",\"y\":\"y\"}]}"
    host_storage_account_name = "bankabroker01"
  }
}

run "broker_mode_by_default" {
  command = apply

  assert {
    condition     = output.datasets["bank-a/credit-applications"].container == "credit-applications" && output.datasets["bank-a/bureau.v2"].container == "bureau-v2"
    error_message = "Container names must be derived from the dataset names."
  }

  assert {
    condition     = output.audience == "api://depa-federation-broker/bankadepadata01"
    error_message = "Broker mode must default the audience to the broker audience."
  }

  assert {
    condition     = output.datasets["bank-a/credit-applications"].federation.azure == { broker_url = "https://bank-a-broker.azurewebsites.net", audience = "api://depa-federation-broker/bankadepadata01" }
    error_message = "Descriptor federation.azure must be {broker_url, audience} in broker mode."
  }
}

run "entra_mode" {
  command = plan

  variables {
    federation_mode = "entra"
    broker          = null
  }

  assert {
    condition     = output.audience == "api://AzureADTokenExchange"
    error_message = "Entra federation audience must be api://AzureADTokenExchange."
  }

  assert {
    condition     = output.broker_url == null
    error_message = "No broker in entra mode."
  }
}

run "broker_settings_required" {
  command = plan

  variables {
    broker = null
  }

  expect_failures = [var.broker]
}

run "rejects_colliding_containers" {
  command = plan

  variables {
    datasets = {
      "bank-a/bureau.v2" = {}
      "bank-a/bureau_v2" = {}
    }
  }

  expect_failures = [var.datasets]
}

run "owner_wildcard_in_broker_mode" {
  command = apply

  variables {
    datasets = {
      "tdp-1/*" = { container_name = "datasets" }
    }
  }

  assert {
    condition     = output.datasets["tdp-1/*"].container == "datasets"
    error_message = "An owner wildcard maps to its explicit container."
  }
}

run "owner_wildcard_needs_container" {
  command = plan

  variables {
    datasets = {
      "tdp-1/*" = {}
    }
  }

  expect_failures = [var.datasets]
}
