# Rendered by deploy/terraform/scripts/validate.sh (and the GitHub workflows) to use a local
# checkout of the service repository instead of the pinned git source.
module "kms" {
  source = "${DEPA_KMS_AZURE_DIR}//deploy/terraform/stacks/1-core"
}
