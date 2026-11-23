# Rendered by deploy/terraform/scripts/validate.sh (and the deploy workflows) to use local
# checkouts of the DC service repositories instead of the pinned git sources.
module "kms" {
  source = "${DEPA_KMS_AZURE_DIR}//deploy/terraform/stacks/1-core"
}

module "contract_service" {
  source = "${DEPA_CONTRACT_SERVICE_DIR}//deploy/terraform/stacks/1-core"
}
