output "resource_group_name" {
  description = "Consumer resource group."
  value       = azurerm_resource_group.this.name
}

output "storage" {
  description = "The consumer's own dataset storage (render-env.sh)."
  value = {
    storage_account_name = module.datasets.storage_account_name
    audience             = module.datasets.audience
    broker_url           = module.datasets.broker_url
    datasets             = module.datasets.datasets
  }
}

output "output_storage" {
  description = "Output storage facts and the contract output.location (render-env.sh)."
  value = {
    storage_account_name = module.outputs.storage_account_name
    audience             = module.outputs.audience
    broker_url           = module.outputs.broker_url
    location             = module.outputs.output_location
  }
}

output "brokers" {
  description = "Broker function apps for deploy/scripts/deploy-brokers.sh."
  value = concat(
    module.datasets.broker_deployment == null ? [] : [module.datasets.broker_deployment],
    module.outputs.broker_deployment == null ? [] : [module.outputs.broker_deployment],
  )
}
