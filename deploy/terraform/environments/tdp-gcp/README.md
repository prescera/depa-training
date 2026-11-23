# Environment: TDP on GCP (GCS)

Example root configuration for a Training Data Provider hosting `dedf/1`
encrypted datasets on GCS, readable only by attested CCRs that exchange a DC
KMS token with `sub = dataset:<dataset_id>` through GCP STS (spec §10). Uses
[`modules/gcp/tdp-storage`](../../modules/gcp/tdp-storage).

## Prerequisites

- The DC KMS issuer URL serves its discovery document with a publicly trusted
  certificate.
- Credentials (e.g. `gcloud auth application-default login`) with
  `roles/storage.admin`, `roles/iam.workloadIdentityPoolAdmin` and
  `roles/serviceusage.serviceUsageAdmin` on the project.
- Dataset ids must be <= 119 characters (`google.subject` limit of 127 bytes for
  `dataset:<id>`).

## Apply order

```bash
cd deploy/terraform/environments/tdp-gcp
cp terraform.tfvars.example terraform.tfvars   # edit values
# optional: uncomment the gcs backend in versions.tf
terraform init
terraform plan -out tfplan
terraform apply tfplan      # also enables iam, sts and storage APIs
terraform output -json datasets
```

A deleted Workload Identity Pool id stays reserved for 30 days; set
`workload_identity_pool_id` on the module if you need to recreate sooner.

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
