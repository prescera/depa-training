# Rendered to local_override.tf (gitignored) by deploy/terraform/scripts/validate.sh
# when DEPA_KMS_AZURE_DIR / DEPA_CONTRACT_SERVICE_DIR are set, or manually:
#   envsubst < local_override.tf.tpl > local_override.tf
module "kms" {
  source = "${DEPA_KMS_AZURE_DIR}//deploy/terraform/stacks/1-core"
}

module "contract_service" {
  source = "${DEPA_CONTRACT_SERVICE_DIR}//deploy/terraform/stacks/1-core"
}
