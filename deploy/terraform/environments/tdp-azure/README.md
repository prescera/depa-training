# Environment: TDP on Azure (Blob)

Example root configuration for a Training Data Provider hosting `dedf/1`
encrypted datasets on Azure Blob: one container per dataset, readable only with a DC KMS
token for `sub = dataset:<dataset_id>`. By default access goes through the TDP-operated
[federation broker](../../modules/azure/federation-broker) (spec §10.1: the KMS signs ES256,
which Entra federated credentials do not accept); `federation_mode = "entra"` switches to
native federated identities once the KMS issues RS256. Uses
[`modules/azure/tdp-storage`](../../modules/azure/tdp-storage).

## Prerequisites

- The DC KMS JWKS (`GET <issuer>/jwks`, cross-checked against the KMS `GET /info` on the
  ledger endpoint) to pin into the broker (`kms_jwks_json`).
- Python 3 with pip (to build the broker package).
- `az login` as a principal with *Contributor* and *Role Based Access Control
  Administrator* (or *User Access Administrator*) on the subscription or target
  resource group.
- The provider uses `storage_use_azuread = true` because shared keys are
  disabled on the account. If you set `allowed_ip_rules`, include the IP of
  the machine running Terraform.

## Apply order

```bash
cd deploy/terraform/environments/tdp-azure
../../modules/azure/federation-broker/build-package.sh   # prints dist/federation-broker-<hash>.zip
cp terraform.tfvars.example terraform.tfvars   # edit values; broker_package_path = printed path
# optional: uncomment the azurerm backend in versions.tf
terraform init
terraform plan -out tfplan
terraform apply tfplan
terraform output -json datasets
```

Uploading in step 2 below needs *Storage Blob Data Contributor* for your own
operator identity on the target container (not granted by this configuration).

## After apply: register the datasets with the DC

For each dataset, using the `depa` CLI (`sdk/python`, see `depa --help`) with the
TDP signing key registered with the DC:

1. Create the dataset key at the KMS (`POST /datasets`, op `dataset.create`) and
   note the key id `<dataset_id>#1` (spec §7.1).
2. Encrypt the plaintext files to `dedf/1` (manifest, `manifest.jws`,
   `objects/*.dedf`) and upload them to the `location.uri` from
   `terraform output -json datasets` (spec §7.3). Uploading needs your own
   operator credentials; the federated identities created here are read-only.
3. Set the Release Policy (`PUT /datasets/{id}/policy`) and include
   `terraform output -raw audience` in `allowed_audiences`; without it
   the KMS refuses to mint storage tokens for this dataset (spec §10).
   In broker mode this is the broker audience (`api://depa-federation-broker/<account>`).
4. Publish the Dataset Descriptor to the CS catalog (`POST /catalog/datasets`,
   op `catalog.publish`) with `location` and `federation` copied from
   `terraform output -json datasets` and the `manifest_digest` from step 2.
