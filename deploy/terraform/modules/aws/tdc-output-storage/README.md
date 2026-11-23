# aws/tdc-output-storage

Training Data Consumer output bucket on S3. CCRs holding a DC-KMS-issued token
with `sub = output:<party_id>` (spec §10) can **create** objects under `prefix`
(no read, list or delete; versioning keeps any overwritten version). Outputs are
`dedf/1` encrypted by the CCR (spec §11). See [federated-storage](../federated-storage).

## Inputs (main)

| Name | Description |
|---|---|
| `name_prefix`, `bucket_name`, `oidc_issuer_url` | As in `tdp-storage`. |
| `party_id` | TDC party id (spec §3.1). |
| `prefix` | Key prefix for outputs (`""` = bucket root). |
| `create_oidc_provider` / `existing_oidc_provider_arn` | Set `false` + the ARN when `tdp-storage` (or another stack) in the same account already created the provider. |
| `kms_key_arn`, `access_log_bucket`, ... | Same optional settings as `tdp-storage`. |

## Outputs

- `output_location`: the contract `output.location` object (spec §8.1).
- `allowed_audience` (`sts.amazonaws.com`): must be in the KMS
  `set_oidc_settings.allowed_audiences_global`.
- `role_arn`, `bucket_name`, `oidc_provider_arn`.

Tests: `terraform test` (offline, mocked provider).
