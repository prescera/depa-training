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
  #   resource_group_name  = "<tdc>-tfstate-rg"
  #   storage_account_name = "<tdc>tfstate"
  #   container_name       = "tfstate"
  #   key                  = "depa-training/tdc-azure-aks/cluster.tfstate"
  #   use_azuread_auth     = true
  # }
}

provider "azurerm" {
  subscription_id = var.subscription_id
  features {}
}

data "azurerm_kubernetes_cluster" "this" {
  name                = var.cluster_name
  resource_group_name = var.resource_group_name
}

locals {
  # Local accounts are disabled: authenticate with Entra ID through kubelogin. The server id is
  # the fixed application id of the AKS Entra server.
  kube_host = data.azurerm_kubernetes_cluster.this.kube_config[0].host
  kube_ca   = base64decode(data.azurerm_kubernetes_cluster.this.kube_config[0].cluster_ca_certificate)
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
