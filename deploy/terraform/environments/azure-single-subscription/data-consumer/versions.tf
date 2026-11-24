terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.81"
    }
  }

  # Remote state. The GitHub workflows render a backend_override.tf with the state account and the
  # key depa-training/<environment>/data-consumer.tfstate; for local use uncomment and fill in:
  # backend "azurerm" {
  #   resource_group_name  = "<state-rg>"
  #   storage_account_name = "<statestorage>"
  #   container_name       = "tfstate"
  #   key                  = "depa-training/<environment>/data-consumer.tfstate"
  #   use_azuread_auth     = true
  # }
}

provider "azurerm" {
  subscription_id     = var.subscription_id
  storage_use_azuread = true
  features {}
}
