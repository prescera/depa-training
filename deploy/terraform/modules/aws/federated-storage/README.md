# aws/federated-storage (internal)

Shared implementation behind [`tdp-storage`](../tdp-storage) and
[`tdc-output-storage`](../tdc-output-storage). Use those wrappers; this module
is not meant to be called directly.

Provisions:

- S3 bucket: `BucketOwnerEnforced`, all four public-access blocks, versioning,
  SSE-S3 (or SSE-KMS with `kms_key_arn` + bucket keys), bucket policy denying
  non-TLS and TLS < 1.2, abort of incomplete multipart uploads after 7 days,
  optional non-current version expiry and server access logging.
- IAM OIDC provider for the DC KMS issuer (`client_id_list = ["sts.amazonaws.com"]`),
  or reuse of an existing one (`create_oidc_provider = false` +
  `existing_oidc_provider_arn`; AWS allows one provider per issuer URL per account).
- One IAM role per entry of `grants` (keyed by OIDC `sub`), trust policy
  `sts:AssumeRoleWithWebIdentity` with `StringEquals` on `<issuer>:aud =
  sts.amazonaws.com` and `<issuer>:sub = <subject>` (spec §10), and an inline
  policy confined to `arn:aws:s3:::<bucket>/<prefix>*`:
  - `read`: `s3:GetObject`, `s3:GetObjectVersion` (no `ListBucket`).
  - `write`: `s3:PutObject`, `s3:AbortMultipartUpload`, `s3:ListMultipartUploadParts`.
  - with a CMK: `kms:Decrypt` (+ `kms:GenerateDataKey` for write) restricted by
    `kms:ViaService` and the S3 encryption context.

Outputs: `bucket_name`, `bucket_arn`, `region`, `oidc_provider_arn`, `audience`,
`grants` (per subject: `uri`, `role_arn`, `role_name`, `access`, `prefix`).
