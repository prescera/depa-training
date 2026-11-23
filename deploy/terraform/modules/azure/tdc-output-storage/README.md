# azure/tdc-output-storage

Training Data Consumer output container on Azure Blob, writable only with a KMS token whose
`sub = output:<party_id>`, through a custom write-only data role (`blobs/write`,
`blobs/add/action`; no read, list or delete) on the container. Default
`federation_mode = "broker"`: the [federation broker](../federation-broker) returns a
create+write (`cw`) user-delegation SAS (spec §10.1). `"entra"`: a federated user-assigned
identity holds the role (enable once the KMS issues RS256). See
[federated-storage](../federated-storage).

Inputs: as `tdp-storage` (incl. `federation_mode`, `broker`), with `party_id` (spec §3.1)
and `container_name` (default `outputs`) instead of `datasets`.

Outputs: `output_location` (contract `output.location`, spec §8.1, with
`federation.azure`), `audience` (add to KMS `allowed_audiences_global`), `broker_url`,
`storage_account_name`.

Requires `storage_use_azuread = true` on the azurerm provider, and the deploying
principal needs `Microsoft.Authorization/roleDefinitions/write` (e.g. *User Access
Administrator*) to create the write-only custom role. Tests:
`terraform test` (offline, mocked provider).
