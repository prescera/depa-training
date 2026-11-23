# Environment: TDC compute on GCP Confidential Space (`gcp-cs-tdx`)

Uses [`modules/gcp/ccr-confidential-space`](../../modules/gcp/ccr-confidential-space)
(network, egress allowlist, workload SA, job-submitter IAM),
[`modules/gcp/tdc-output-storage`](../../modules/gcp/tdc-output-storage) (output bucket with
`output:<party_id>` federation) and optionally the
[`instance-template`](../../modules/gcp/ccr-confidential-space/instance-template) sub-module
for Terraform-managed runs.

## Prerequisites

- A dedicated project; credentials (`gcloud auth application-default login`) with
  `roles/owner` or: Compute Admin, Service Account Admin, Project IAM Admin, Role Admin,
  DNS Admin, Storage Admin, Workload Identity Pool Admin, Service Usage Admin.
- C3 quota in `region`; the DC KMS issuer reachable with a publicly trusted certificate.

## Apply order

```bash
cd deploy/terraform/environments/tdc-gcp-cs
cp terraform.tfvars.example terraform.tfvars      # edit
# optional: uncomment the gcs backend in versions.tf
terraform init
terraform plan -out tfplan
terraform apply tfplan                             # enables APIs, then creates the rest
terraform output
```

## After apply

1. Ask the DC to add `terraform output -raw output_allowed_audience` to the KMS
   `allowed_audiences_global`; use `terraform output -json output_location` as the contract
   `output.location`.
2. Submit runs with the `depa` CLI (release manifest `platform = gcp-cs-tdx`), passing
   `--service-account=$(terraform output -raw workload_service_account_email)`,
   `--subnet=$(terraform output -raw subnetwork_self_link)` and `--no-address`; or set
   `ccr_runs` and create the VM from `ccr_run_templates`.
3. VMs delete themselves when the CCR exits or `max_run_duration` elapses.
