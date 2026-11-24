# Offline plan of the whole phase: providers are mocked, nothing contacts Azure or the cluster.
# Catches plan-time errors that `terraform validate` cannot see. Run with `terraform test`.
mock_provider "azurerm" {
  mock_data "azurerm_kubernetes_cluster" {
    defaults = {
      kube_config = [{
        host                   = "https://demo1-tdc-ccr-aks.hcp.centralindia.azmk8s.io:443"
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
    name_prefix                = "demo1"
    suffix                     = "ab12"
    party_resource_groups      = { "dc-member-1" = "demo1-dc-member-1-rg", tdc = "demo1-tdc-rg", "tdp-1" = "demo1-tdp-1-rg", "tdp-2" = "demo1-tdp-2-rg", "tdp-3" = "demo1-tdp-3-rg", "tdp-4" = "demo1-tdp-4-rg" }
    log_analytics_workspace_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/demo1-dc-rg/providers/Microsoft.OperationalInsights/workspaces/demo1-law"
    kms = {
      ledger_name               = "demo1-kms-cin"
      ledger_endpoint           = "https://demo1-kms-cin.confidential-ledger.azure.com"
      identity_service_endpoint = "https://identity.confidential-ledger.core.azure.com/ledgerIdentity/demo1-kms-cin"
      resource_group_name       = "demo1-dc-kms-rg"
    }
    cs = {
      ledger_name               = "demo1-cs-cin"
      ledger_endpoint           = "https://demo1-cs-cin.confidential-ledger.azure.com"
      identity_service_endpoint = "https://identity.confidential-ledger.core.azure.com/ledgerIdentity/demo1-cs-cin"
      resource_group_name       = "demo1-dc-cs-rg"
    }
    registry = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/demo1-tdc-rg/providers/Microsoft.ContainerRegistry/registries/demo1ab12acr"
      login_server = "demo1ab12acr.azurecr.io"
    }
    compute = {
      party_id                = "tdc"
      resource_group_name     = "demo1-tdc-rg"
      cluster_name            = "demo1-tdc-ccr-aks"
      aci_resource_group_name = "demo1-tdc-ccr-aci-rg"
      aci_subnet_name         = "cg"
      acr_pull_identity = {
        id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/demo1-tdc-rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/demo1-tdc-ccr-acrpull-mi"
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
  job_submitter_object_ids = ["33333333-3333-3333-3333-333333333333"]
  operator_object_ids      = ["33333333-3333-3333-3333-333333333333"]
}

run "plan" {
  command = plan
}
