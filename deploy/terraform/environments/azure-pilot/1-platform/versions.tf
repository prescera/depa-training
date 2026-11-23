terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.81"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
  }

  # Remote state (required for the GitHub workflows; they pass -backend-config):
  # backend "azurerm" {
  #   resource_group_name  = "<state-rg>"
  #   storage_account_name = "<statestorage>"
  #   container_name       = "tfstate"
  #   key                  = "depa-training/azure-pilot/1-platform.tfstate"
  #   use_azuread_auth     = true
  # }
}

provider "azurerm" {
  subscription_id     = var.subscription_id
  storage_use_azuread = true
  features {}
}
