terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.81"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.3"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 3.5"
    }
  }

  # backend "azurerm" {
  #   resource_group_name  = "<state-rg>"
  #   storage_account_name = "<statestorage>"
  #   container_name       = "tfstate"
  #   key                  = "depa-training/azure-pilot/2-workloads.tfstate"
  #   use_azuread_auth     = true
  # }
}

provider "azurerm" {
  subscription_id     = var.platform.subscription_id
  storage_use_azuread = true # data and broker host accounts have shared keys disabled
  features {}
}

data "azurerm_kubernetes_cluster" "compute" {
  count = var.platform.compute == null ? 0 : 1

  name                = var.platform.compute.cluster_name
  resource_group_name = var.platform.compute.resource_group_name
}

locals {
  # Local accounts are disabled on the cluster: Entra ID via kubelogin (fixed AKS server app id).
  kube_host = one(data.azurerm_kubernetes_cluster.compute[*].kube_config[0].host)
  kube_ca   = try(base64decode(data.azurerm_kubernetes_cluster.compute[0].kube_config[0].cluster_ca_certificate), null)
  kube_exec = {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "kubelogin"
    args        = ["get-token", "--login", var.kubelogin_login_mode, "--server-id", "6dae42f8-4368-4678-94ff-3960e28e3630"]
  }
}

provider "kubernetes" {
  host                   = local.kube_host
  cluster_ca_certificate = local.kube_ca

  exec {
    api_version = local.kube_exec.api_version
    command     = local.kube_exec.command
    args        = local.kube_exec.args
  }
}

provider "helm" {
  kubernetes = {
    host                   = local.kube_host
    cluster_ca_certificate = local.kube_ca
    exec                   = local.kube_exec
  }
}
