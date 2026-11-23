terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.81"
    }
  }

  # Remote state (recommended). Create the storage account out of band, then uncomment and
  # run `terraform init -migrate-state`.
  # backend "azurerm" {
  #   resource_group_name  = "<tdc>-tfstate-rg"
  #   storage_account_name = "<tdc>tfstate"
  #   container_name       = "tfstate"
  #   key                  = "depa-training/tdc-azure-aks/infra.tfstate"
  #   use_azuread_auth     = true
  # }
}

provider "azurerm" {
  subscription_id     = var.subscription_id
  storage_use_azuread = true # the output storage account has shared keys disabled
  features {}
}
