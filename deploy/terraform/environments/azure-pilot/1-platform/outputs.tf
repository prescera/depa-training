output "pilot_platform" {
  description = "Everything 2-workloads, render-env.sh and the workflows need (pass as the `platform` variable of 2-workloads)."
  value = {
    subscription_id = var.subscription_id
    tenant_id       = var.tenant_id
    location        = var.location
    name_prefix     = var.name_prefix
    suffix          = local.suffix
    party_resource_groups = {
      for id, rg in azurerm_resource_group.party : id => rg.name
    }
    log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
    kms = {
      ledger_name               = module.kms.ledger_name
      ledger_endpoint           = module.kms.ledger_endpoint
      identity_service_endpoint = module.kms.identity_service_endpoint
      resource_group_name       = module.kms.resource_group_name
    }
    cs = {
      ledger_name               = module.contract_service.ledger_name
      ledger_endpoint           = module.contract_service.ledger_endpoint
      identity_service_endpoint = module.contract_service.identity_service_endpoint
      resource_group_name       = module.contract_service.resource_group_name
    }
    registry = {
      id           = azurerm_container_registry.this.id
      login_server = azurerm_container_registry.this.login_server
    }
    compute = local.compute == null ? null : {
      party_id                = local.compute
      resource_group_name     = module.compute[0].resource_group_name
      cluster_name            = module.compute[0].cluster_name
      aci_resource_group_name = module.compute[0].aci_resource_group_name
      aci_subnet_name         = module.compute[0].aci_subnet_name
      acr_pull_identity       = module.compute[0].acr_pull_identity
    }
  }
}

output "kms_gateway_stack_inputs" {
  description = "Input for environments/dc/2-gateway if a publicly trusted KMS issuer is added later (AWS/GCP storage)."
  value       = module.kms.gateway_stack_inputs
}
