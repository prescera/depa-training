# gcp/tdc-output-storage

Training Data Consumer output bucket on GCS. The federated principal for
`sub = output:<party_id>` gets `roles/storage.objectCreator` (create only, no
read, overwrite or delete) on `prefix`. Default pool id `<name_prefix>-out`.
See [federated-storage](../federated-storage).

Inputs: as `tdp-storage`, with `party_id` (spec §3.1) and `prefix` (`""` = root)
instead of `datasets`.

Outputs: `output_location` (contract `output.location`, spec §8.1),
`allowed_audience` (add to KMS `allowed_audiences_global`), `principal`,
`bucket_name`, `workload_identity_provider_name`.

Tests: `terraform test` (offline, mocked provider).
