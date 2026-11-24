# Offline plan of the whole phase: providers are mocked, nothing contacts Azure or the cluster.
# Catches plan-time errors that `terraform validate` cannot see. Run with `terraform test`.
mock_provider "azurerm" {
  mock_data "azurerm_kubernetes_cluster" {
    defaults = {
      kube_config = [{
        host                   = "https://pilot1-tdc-ccr-aks.hcp.centralindia.azmk8s.io:443"
        cluster_ca_certificate = "LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0tCk1JSUIKLS0tLS1FTkQgQ0VSVElGSUNBVEUtLS0tLQo="
      }]
    }
  }
  mock_data "azurerm_client_config" {
    defaults = {
      client_id       = "44444444-4444-4444-4444-444444444444"
      object_id       = "55555555-5555-5555-5555-555555555555"
      tenant_id       = "11111111-1111-1111-1111-111111111111"
      subscription_id = "00000000-0000-0000-0000-000000000000"
    }
  }
}
mock_provider "kubernetes" {}
mock_provider "helm" {}

variables {
  platform = {
    subscription_id            = "00000000-0000-0000-0000-000000000000"
    tenant_id                  = "11111111-1111-1111-1111-111111111111"
    location                   = "centralindia"
    name_prefix                = "pilot1"
    suffix                     = "ab12"
    party_resource_groups      = { "dc-member-1" = "pilot1-dc-member-1-rg", tdc = "pilot1-tdc-rg", "tdp-1" = "pilot1-tdp-1-rg", "tdp-2" = "pilot1-tdp-2-rg", "tdp-3" = "pilot1-tdp-3-rg", "tdp-4" = "pilot1-tdp-4-rg" }
    log_analytics_workspace_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/pilot1-dc-rg/providers/Microsoft.OperationalInsights/workspaces/pilot1-law"
    kms = {
      ledger_name               = "pilot1-kms-cin"
      ledger_endpoint           = "https://pilot1-kms-cin.confidential-ledger.azure.com"
      identity_service_endpoint = "https://identity.confidential-ledger.core.azure.com/ledgerIdentity/pilot1-kms-cin"
      resource_group_name       = "pilot1-dc-kms-rg"
    }
    cs = {
      ledger_name               = "pilot1-cs-cin"
      ledger_endpoint           = "https://pilot1-cs-cin.confidential-ledger.azure.com"
      identity_service_endpoint = "https://identity.confidential-ledger.core.azure.com/ledgerIdentity/pilot1-cs-cin"
      resource_group_name       = "pilot1-dc-cs-rg"
    }
    registry = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/pilot1-tdc-rg/providers/Microsoft.ContainerRegistry/registries/pilot1ab12acr"
      login_server = "pilot1ab12acr.azurecr.io"
    }
    compute = {
      party_id                = "tdc"
      resource_group_name     = "pilot1-tdc-rg"
      cluster_name            = "pilot1-tdc-ccr-aks"
      aci_resource_group_name = "pilot1-tdc-ccr-aci-rg"
      aci_subnet_name         = "cg"
      acr_pull_identity = {
        id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/pilot1-tdc-rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/pilot1-tdc-ccr-acrpull-mi"
        principal_id = "66666666-6666-6666-6666-666666666666"
      }
    }
  }
  parties = [
    { party_id = "dc-member-1", roles = ["dc-member"] },
    { party_id = "tdc", roles = ["tdc", "tdp"], compute = true, datasets = { "tdc/*" = { container_name = "datasets" } } },
    { party_id = "tdp-1", roles = ["tdp"], datasets = { "tdp-1/*" = { container_name = "datasets" } } },
    { party_id = "tdp-2", roles = ["tdp"], datasets = { "tdp-2/*" = { container_name = "datasets" } } },
    { party_id = "tdp-3", roles = ["tdp"], datasets = { "tdp-3/*" = { container_name = "datasets" } } },
    { party_id = "tdp-4", roles = ["tdp"], datasets = { "tdp-4/*" = { container_name = "datasets" } } },
  ]
  kms_jwks_json            = "{\"keys\":[{\"kty\":\"EC\",\"crv\":\"P-256\",\"kid\":\"k1\",\"x\":\"f83OJ3D2xF1Bg8vub9tLe1gHMzV76e8Tus9uPHvRVEU\",\"y\":\"x_FEzRu9m36HLN_tue659LNpXW6pCyStikYjKIWI5a0\"}]}"
  broker_package_path      = "tests/fixtures/broker.zip"
  job_submitter_object_ids = ["33333333-3333-3333-3333-333333333333"]
  operator_object_ids      = ["33333333-3333-3333-3333-333333333333"]
}

run "plan" {
  command = plan
}
