# aws/tdp-storage

Training Data Provider storage on S3 for `dedf/1` encrypted datasets (spec §7.3),
readable **only** by CCRs holding a DC-KMS-issued token with
`sub = dataset:<dataset_id>` (spec §10). One bucket, one prefix and one
read-only IAM role per dataset. See [federated-storage](../federated-storage)
for the exact resources.

## Inputs (main)

| Name | Description |
|---|---|
| `name_prefix` | Prefix for IAM role names. |
| `bucket_name` | Globally unique bucket name. |
| `oidc_issuer_url` | KMS issuer (`set_oidc_settings.issuer_url`), https, no trailing slash. |
| `datasets` | `{ "<owner>/<name>" = { prefix = optional } }`; default prefix `<dataset_id>/`. |
| `create_oidc_provider` / `existing_oidc_provider_arn` | Create or reuse the account's IAM OIDC provider for the issuer. |
| `kms_key_arn` | Optional CMK for SSE-KMS (default SSE-S3). |
| `access_log_bucket`, `noncurrent_version_expiration_days`, `permissions_boundary_arn`, `max_session_duration`, `force_destroy`, `tags` | Optional hardening/ops settings. |

## Outputs

- `datasets[<id>].location` / `.federation`: paste into the CS Dataset Descriptor
  (spec §7.2: `location = {connector = "s3", uri, region}`, `federation.aws.role_arn`).
- `allowed_audience` (`sts.amazonaws.com`): must be in the dataset Release Policy
  `allowed_audiences` (spec §7.1).
- `bucket_name`, `oidc_provider_arn`.

## Example

```hcl
module "tdp_storage" {
  source          = "git::https://github.com/prescera/depa-training.git//deploy/terraform/modules/aws/tdp-storage?ref=main"
  name_prefix     = "bank-a-prod"
  bucket_name     = "bank-a-depa-datasets-prod"
  oidc_issuer_url = "https://kms.dc.example.org/app/oidc"
  datasets        = { "bank-a/credit-applications" = {} }
}
```

Tests: `terraform test` (offline, mocked provider).
