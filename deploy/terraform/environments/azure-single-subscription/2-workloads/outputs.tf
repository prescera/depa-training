output "workloads" {
  description = "Data-plane and cluster facts for deploy/scripts/render-env.sh."
  value = {
    kms_oidc_issuer_url = local.issuer
    storage = {
      for id, m in module.tdp_storage : id => {
        storage_account_name = m.storage_account_name
        audience             = m.audience
        broker_url           = m.broker_url
        datasets             = m.datasets
      }
    }
    outputs = {
      for id, m in module.tdc_output_storage : id => {
        storage_account_name = m.storage_account_name
        audience             = m.audience
        broker_url           = m.broker_url
        location             = m.output_location
      }
    }
    # Broker function apps, deployed after apply with deploy/scripts/deploy-brokers.sh.
    brokers = concat(
      [for m in values(module.tdp_storage) : m.broker_deployment if m.broker_deployment != null],
      [for m in values(module.tdc_output_storage) : m.broker_deployment if m.broker_deployment != null],
    )
    compute = var.platform.compute == null ? null : {
      party_id       = var.platform.compute.party_id
      resource_group = var.platform.compute.resource_group_name
      cluster_name   = var.platform.compute.cluster_name
      namespace      = module.cluster[0].ccr_namespace
    }
  }
}
