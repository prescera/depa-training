# gcp/tdp-storage

Training Data Provider storage on GCS for `dedf/1` encrypted datasets, readable
only by CCRs holding a DC-KMS-issued token with `sub = dataset:<dataset_id>`
exchanged through GCP STS (spec §10). One bucket, one prefix per dataset, one
Workload Identity Pool/provider per module instance. See
[federated-storage](../federated-storage).

## Inputs (main)

| Name | Description |
|---|---|
| `name_prefix` | Used for the default pool id `<name_prefix>-tdp`. |
| `project_id`, `project_number` | Hosting project (number looked up when null). |
| `bucket_name`, `location` | Bucket name and location. |
| `oidc_issuer_url` | KMS issuer, https, no trailing slash. |
| `datasets` | `{ "<owner>/<name>" = { prefix = optional } }`; default prefix `<dataset_id>/`; ids <= 119 chars. |
| `allowed_audiences` | Optional; empty = provider default audience. |
| `kms_key_name`, `retention_period_seconds`, `soft_delete_retention_seconds`, `noncurrent_version_expiration_days`, `labels`, `force_destroy` | Optional. |

## Outputs

- `datasets[<id>].location` / `.federation`: CS Dataset Descriptor fragments
  (`connector = "gcs"`, `federation.gcp.audience`).
- `allowed_audience`: must be in the dataset Release Policy `allowed_audiences`.
- `bucket_name`, `workload_identity_provider_name`.

Required APIs: `iam.googleapis.com`, `sts.googleapis.com`, `storage.googleapis.com`.

Tests: `terraform test` (offline, mocked provider).
