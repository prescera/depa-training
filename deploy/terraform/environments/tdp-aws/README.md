# Environment: TDP on AWS (S3)

Example root configuration for a Training Data Provider hosting `dedf/1`
encrypted datasets on S3, readable only by attested CCRs that hold a DC KMS
token with `sub = dataset:<dataset_id>` (spec §10). Uses
[`modules/aws/tdp-storage`](../../modules/aws/tdp-storage).

## Prerequisites

- The DC has published its KMS issuer URL, and
  `curl -fsS <issuer>/.well-known/openid-configuration` succeeds with a
  publicly trusted certificate (AWS STS fetches the JWKS from it).
- AWS credentials for the TDP account with rights to manage S3, IAM roles and
  IAM OIDC providers.
- Only one IAM OIDC provider per issuer URL can exist per account: if one exists
  already (e.g. TDC output storage in the same account), set
  `create_oidc_provider = false` and `existing_oidc_provider_arn`.

## Apply order

```bash
cd deploy/terraform/environments/tdp-aws
cp terraform.tfvars.example terraform.tfvars   # edit values
# optional: uncomment the s3 backend in versions.tf
terraform init
terraform plan -out tfplan
terraform apply tfplan
terraform output -json datasets
```

To add a dataset later, add it to `datasets` and re-run plan/apply; existing
datasets are unaffected (one role per dataset).

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
   `terraform output -raw allowed_audience` in `allowed_audiences`; without it
   the KMS refuses to mint storage tokens for this dataset (spec §10).
4. Publish the Dataset Descriptor to the CS catalog (`POST /catalog/datasets`,
   op `catalog.publish`) with `location` and `federation` copied from
   `terraform output -json datasets` and the `manifest_digest` from step 2.
