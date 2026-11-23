terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.81"
    }
  }

  # Remote state (recommended). Create the state storage account out of band,
  # then uncomment and run `terraform init -migrate-state`.
  # backend "azurerm" {
  #   resource_group_name  = "<tdp>-tfstate-rg"
  #   storage_account_name = "<tdp>tfstate"
  #   container_name       = "tfstate"
  #   key                  = "depa-training/tdp-azure.tfstate"
  #   use_azuread_auth     = true
  # }
}

provider "azurerm" {
  features {}

  subscription_id = var.subscription_id
  # The dataset storage account has shared keys disabled.
  storage_use_azuread = true
}
