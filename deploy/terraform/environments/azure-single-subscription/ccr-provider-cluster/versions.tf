terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.81"
    }
  }

  # Remote state. The GitHub workflows render a backend_override.tf with the state account and the
  # key depa-training/<environment>/ccr-provider-cluster.tfstate; for local use uncomment and fill in:
  # backend "azurerm" {
  #   resource_group_name  = "<state-rg>"
  #   storage_account_name = "<statestorage>"
  #   container_name       = "tfstate"
  #   key                  = "depa-training/<environment>/ccr-provider-cluster.tfstate"
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
