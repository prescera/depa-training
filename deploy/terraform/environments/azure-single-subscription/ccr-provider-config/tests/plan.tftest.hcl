# Offline plan: providers are mocked, nothing contacts Azure. Run with `terraform test`.
mock_provider "azurerm" {
  mock_data "azurerm_kubernetes_cluster" {
    defaults = {
      kube_config = [{
        host                   = "https://demo1-ccr-ccr-aks.hcp.centralindia.azmk8s.io:443"
        cluster_ca_certificate = "LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0tCk1JSUIKLS0tLS1FTkQgQ0VSVElGSUNBVEUtLS0tLQo="
      }]
    }
  }
}
mock_provider "helm" {}
mock_provider "kubernetes" {}


variables {
  cluster = {
    resource_group_name     = "demo1-ccr-provider-rg"
    cluster_name            = "demo1-ccr-ccr-aks"
    aci_resource_group_name = "demo1-ccr-ccr-aci-rg"
    aci_subnet_name         = "cg"
  }
  subscription_id          = "00000000-0000-0000-0000-000000000000"
  name_prefix              = "demo1"
  job_submitter_object_ids = ["33333333-3333-3333-3333-333333333333"]
}

run "plan" {
  command = plan

  assert {
    condition     = output.compute.namespace == "depa-ccr"
    error_message = "Clean rooms run in the depa-ccr namespace."
  }
}
