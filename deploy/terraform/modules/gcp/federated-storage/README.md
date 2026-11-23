# gcp/federated-storage (internal)

Shared implementation behind [`tdp-storage`](../tdp-storage) and
[`tdc-output-storage`](../tdc-output-storage). Use those wrappers.

Provisions:

- GCS bucket: uniform bucket-level access, public access prevention `enforced`,
  versioning, soft delete, abort of incomplete multipart uploads after 7 days,
  optional CMEK, retention policy and non-current version expiry.
- Workload Identity Pool + OIDC provider for the DC KMS issuer:
  `google.subject = assertion.sub`, `attribute_condition` accepting only the
  subjects granted by this instance, optional `allowed_audiences` (empty = the
  provider's default audience `//iam.googleapis.com/<provider name>`).
- One bucket IAM binding per grant to
  `principal://iam.googleapis.com/projects/<number>/locations/global/workloadIdentityPools/<pool>/subject/<sub>`
  (direct resource access, no service-account impersonation), confined by an IAM
  condition on `resource.name.startsWith("projects/_/buckets/<bucket>/objects/<prefix>")`:
  - `read`: `roles/storage.objectViewer` (object listing is not granted in
    practice: list is evaluated against the bucket and fails the condition).
  - `write`: `roles/storage.objectCreator` (create only; no overwrite/delete).

Limits: `google.subject` is at most 127 bytes, so `dataset:<dataset_id>` must
be <= 127 characters (dataset ids <= 119); the `attribute_condition` must stay
under 4096 characters (split very large dataset sets across instances).

Outputs: `bucket_name`, `bucket_location`, `workload_identity_pool_name`,
`workload_identity_provider_name`, `audience`, `grants` (per subject: `uri`,
`principal`, `role`).
