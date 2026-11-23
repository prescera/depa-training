# Static checks for all Terraform modules and environments. Never contacts cloud
# APIs or remote state: `init -backend=false` + `validate` only.
#
#   make validate                 # fmt -check + init/validate everything
#   make validate ONLY=modules/gcp # restrict to a sub-tree
#   make fmt                      # rewrite files in canonical format
#
# environments/dc consumes the DC service repos through pinned git sources; set
# DEPA_KMS_AZURE_DIR and DEPA_CONTRACT_SERVICE_DIR to validate against local
# checkouts instead (see environments/dc/README.md).

SHELL := /usr/bin/env bash
TERRAFORM ?= terraform
ONLY ?=

.PHONY: validate fmt

validate:
	TERRAFORM=$(TERRAFORM) ./scripts/validate.sh . $(if $(ONLY),--only $(ONLY))

fmt:
	$(TERRAFORM) fmt -recursive .
