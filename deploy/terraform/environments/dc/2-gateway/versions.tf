terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.81"
    }
    http = {
      source  = "hashicorp/http"
      version = "~> 3.6"
    }
  }

  # Remote state (recommended): storage account with Entra ID auth, versioning
  # and soft delete; one key per phase.
  # backend "azurerm" {
  #   resource_group_name  = "<tfstate-rg>"
  #   storage_account_name = "<tfstateaccount>"
  #   container_name       = "tfstate"
  #   key                  = "depa-dc/<environment>/2-gateway.tfstate"
  #   use_azuread_auth     = true
  # }
}

provider "azurerm" {
  subscription_id = var.subscription_id
  tenant_id       = var.tenant_id

  features {
    key_vault {
      purge_soft_delete_on_destroy               = false
      purge_soft_deleted_certificates_on_destroy = false
      recover_soft_deleted_key_vaults            = true
    }
    resource_group {
      prevent_deletion_if_contains_resources = true
    }
  }
}
