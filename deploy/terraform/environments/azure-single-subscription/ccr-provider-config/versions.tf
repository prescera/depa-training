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

  # Remote state. The GitHub workflows render a backend_override.tf with the state account and the
  # key depa-training/<environment>/ccr-provider-config.tfstate; for local use uncomment and fill in:
  # backend "azurerm" {
  #   resource_group_name  = "<state-rg>"
  #   storage_account_name = "<statestorage>"
  #   container_name       = "tfstate"
  #   key                  = "depa-training/<environment>/ccr-provider-config.tfstate"
  #   use_azuread_auth     = true
  # }
}

provider "azurerm" {
  subscription_id     = var.subscription_id
  storage_use_azuread = true
  features {
    # Demo deployment, torn down as a whole: Azure adds resources Terraform does not manage
    # (e.g. Application Insights smart-detection alert rules), which would otherwise block
    # deleting the resource groups.
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
  }
}

# Local accounts are disabled on the cluster: Entra ID via kubelogin (fixed AKS server app id).
locals {
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
