# azure/federated-storage (internal)

Shared implementation behind [`tdp-storage`](../tdp-storage) and
[`tdc-output-storage`](../tdc-output-storage). Use those wrappers.

Provisions:

- Storage account (StorageV2): HTTPS only, TLS 1.2, **shared keys/SAS disabled**,
  Entra ID by default, no anonymous/nested public access, no cross-tenant
  replication, infrastructure (double) encryption, blob versioning, blob and
  container soft delete, copy scope `AAD`, optional IP allowlist (default action
  `Deny` when `allowed_ip_rules` is set).
- One private container per distinct `container_name`.
- Federation, by `federation_mode`:
  - `broker` (default, spec §10.1): the [federation broker](../federation-broker) whose
    signer identity holds the container-scoped roles below and mints user-delegation SAS
    (`rl` read, `cw` write) for verified ES256 KMS tokens. Needs `broker` settings.
  - `entra` (spec §10; enable once the KMS issues RS256): one user-assigned identity per
    grant with a federated credential (`issuer = oidc_issuer_url`, `subject = <sub>`,
    `audience = api://AzureADTokenExchange`) holding the container-scoped role.
- Container-scoped data roles per grant:
  - `read`: *Storage Blob Data Reader*.
  - `write`: custom role *DEPA CCR output writer (<account>)* with only
    `blobs/write` and `blobs/add/action` data actions (no read/list/delete;
    versioning keeps overwritten versions). Created only if a write grant exists.

Requirements for the caller:

- `provider "azurerm" { storage_use_azuread = true }` (shared keys are disabled).
- `entra` mode: the issuer's discovery document and JWKS must be served with a publicly
  trusted TLS certificate (DC App Gateway); Entra ID fetches them during token exchange.
- Customer-managed keys are out of scope (data is client-side encrypted); add an
  `azurerm_storage_account_customer_managed_key` in the caller if required.

Outputs: `storage_account_id`, `storage_account_name`, `audience`, `broker_url`, `grants`
(per subject: `uri`, `container`, `access`, `federation_azure` = `{broker_url, audience}` or
`{tenant_id, client_id}`).
