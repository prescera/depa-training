# azure/tdp-storage

Training Data Provider storage on Azure Blob for `dedf/1` encrypted datasets.
Each dataset gets its own private container. By default (`federation_mode = "broker"`,
spec §10.1) the TDP's [federation broker](../federation-broker) exchanges a KMS token with
`sub = dataset:<dataset_id>` for a read+list user-delegation SAS on that container only.
`federation_mode = "entra"` (enable once the KMS issues RS256) instead creates one
user-assigned identity per dataset whose federated credential trusts only that subject, with
*Storage Blob Data Reader* on the container (spec §10). See
[federated-storage](../federated-storage).

## Inputs (main)

| Name | Description |
|---|---|
| `name_prefix` | Prefix for identity names. |
| `resource_group_name`, `location` | Existing resource group and region. |
| `storage_account_name` | Globally unique, 3-24 lowercase alphanumerics. |
| `oidc_issuer_url` | KMS issuer, https, no trailing slash. |
| `datasets` | `{ "<owner>/<name>" = { container_name = optional } }`; default container = dataset name with `.`/`_` → `-`; must be unique. Broker mode also accepts `"<owner>/*" = { container_name = "datasets" }`: one container for every dataset of the owner (ids not known in advance). |
| `allowed_ip_rules` | Optional IP allowlist (CCR egress IPs + Terraform runner). |
| `federation_mode` | `broker` (default) or `entra`. |
| `broker` | Broker mode: `package_path`, `kms_jwks_json` (pinned), `host_storage_account_name`, optional `audience`, `jwks_refresh`, `sas_max_ttl_seconds`, `log_analytics_workspace_id`. |
| `account_replication_type`, `blob_delete_retention_days`, `container_delete_retention_days`, `tags` | Optional. |

## Outputs

- `datasets[<id>].location` / `.federation`: CS Dataset Descriptor fragments
  (`connector = "azure-blob"`, `federation.azure = {broker_url, audience}` in broker mode,
  `{tenant_id, client_id}` in entra mode).
- `audience` (broker audience, or `api://AzureADTokenExchange` in entra mode) and
  `broker_url`: the audience must be in the dataset Release Policy `allowed_audiences`.
- `storage_account_name`, `storage_account_id`.

The calling configuration must set `storage_use_azuread = true` on the azurerm
provider. Tests: `terraform test` (offline, mocked provider).
