# Rendered by deploy/terraform/scripts/validate.sh (and the GitHub workflows) to use a local
# checkout of the service repository instead of the pinned git source.
module "contract_service" {
  source = "${DEPA_CONTRACT_SERVICE_DIR}//deploy/terraform/stacks/1-core"
}
