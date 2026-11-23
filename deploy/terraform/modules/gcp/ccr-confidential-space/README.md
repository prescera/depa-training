# gcp/ccr-confidential-space

Compute plane for `gcp-cs-tdx` clean rooms: **Confidential Space** VMs on **C3 (Intel TDX)**.
The operator is untrusted; the launcher only runs the digest-pinned image and the launch
policy baked into the image (`tee.launch_policy.*` labels) limits what metadata can change.

| Path | Provisions |
|---|---|
| `.` (this module) | Required APIs; workload service account (`roles/confidentialcomputing.workloadUser`, optional `roles/logging.logWriter` for debug releases, `roles/artifactregistry.reader` on listed repositories — no data access, storage is federated per dataset); VPC + subnet (Private Google Access, flow logs); Cloud Router + NAT; egress firewall targeting the workload SA (deny all, allow 443 to `private.googleapis.com` and to the listed CIDRs); private DNS zones mapping `googleapis.com`, `pkg.dev`, `gcr.io` to `private.googleapis.com`; custom **job-submitter** role (create/get/list/delete instances, serial output, subnet use) + `iam.serviceAccountUser` on the workload SA only. |
| [`instance-template/`](instance-template) | One CCR run as an instance template: C3, `confidential_instance_type = "TDX"`, shielded VM, `pd-balanced`, `confidential-space` image family (or `-debug`), no external IP, `instance_termination_action = "DELETE"`, `max_run_duration`, metadata `tee-image-reference` (digest only), `tee-restart-policy = Never`, `tee-container-log-redirect = false` (unless `debug`), `tee-env-DEPA_*` passthrough with an allowlist, `DEPA_PLATFORM = gcp-cs-tdx`. |

## Key inputs / outputs

Inputs: `project_id`, `region`, `name_prefix`, `egress_https_destination_ranges`,
`job_submitter_members`, `artifact_registry_repositories`, `allow_debug_log_redirect`,
`allow_external_ip` (default false). Outputs: `workload_service_account_email`,
`network_self_link`, `subnetwork_self_link`, `job_submitter_role_id`.

`instance-template/` inputs: `service_account_email`, `subnetwork`, `image_reference`,
`env`, `max_run_duration_seconds`, `machine_type`, `debug`. Output: `self_link`.

## Notes

- Submitters create VMs directly (SDK `depa.deploy.gcp`, which shells out
  to `gcloud compute instances create`) or from a template
  (`--source-instance-template`). Without `allow_external_ip`, the create call must pass
  `--no-address` and `--subnet=<subnetwork_self_link>`.
- Use a dedicated project: the job-submitter role is project-scoped and can delete any VM.
- `support_attributes = ["STABLE"]` in a release requires the production
  `confidential-space` image; debug VMs attest `dbgstat = enabled` and are rejected.
