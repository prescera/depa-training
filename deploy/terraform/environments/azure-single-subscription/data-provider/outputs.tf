output "party_id" {
  description = "Provider slot."
  value       = var.party_id
}

output "resource_group_name" {
  description = "Provider resource group."
  value       = azurerm_resource_group.this.name
}

output "storage" {
  description = "Dataset storage facts for deploy/scripts/render-env.sh."
  value = {
    storage_account_name = module.storage.storage_account_name
    audience             = module.storage.audience
    broker_url           = module.storage.broker_url
    datasets             = module.storage.datasets
  }
}

output "brokers" {
  description = "Broker function apps for deploy/scripts/deploy-brokers.sh ([{resource_group_name, function_app_name}])."
  value       = module.storage.broker_deployment == null ? [] : [module.storage.broker_deployment]
}
