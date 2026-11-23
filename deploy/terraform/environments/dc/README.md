# environments/dc - Data Collaborative governance plane

Composition of the two DC services, each a CCF JavaScript app on its own Azure
Confidential Ledger, consumed from their repositories:

| Phase | KMS (`prescera/depa-training-kms-azure`) | Contract Service (`prescera/contract-service`) |
|---|---|---|
| `1-core/` | `deploy/terraform/stacks/1-core`: RG, VNet, identities, Key Vault (member + bootstrap frontend certs), ledger | `deploy/terraform/stacks/1-core`: RG, Key Vault (member cert), ledger (+ gateway prerequisites if `cs_enable_gateway`) |
| `2-gateway/` | `deploy/terraform/stacks/2-gateway`: ledger private endpoint, ACME storage, App Gateway WAF_v2 for the public OIDC issuer, monitoring | optional, only when `cs_core` is set |

## Module sources and versions

Terraform does not allow variables in `source`, so both services are referenced with
literal git sources tracking `main` of the `prescera` repositories, e.g.
`git::https://github.com/prescera/depa-training-kms-azure.git//deploy/terraform/stacks/1-core?ref=main`.
For a production DC, change `ref` to the reviewed release tag in every module block (app
bundles are versioned separately and approved by digest through governance). The GitHub
workflows never use these sources directly: they check out both service repositories from the
same owner at `DEPA_SERVICES_REF` (default `main`) and activate the local override below.

**Local override** (develop against local checkouts, CI validation):
`local_override.tf.tpl` in each phase is rendered to `local_override.tf`
(gitignored by `deploy/terraform/.gitignore`), which replaces the git sources:

```bash
export DEPA_KMS_AZURE_DIR=$HOME/depa_training/depa-training-kms-azure
export DEPA_CONTRACT_SERVICE_DIR=$HOME/depa_training/contract-service
# static checks (renders and removes the override automatically)
make -C deploy/terraform validate ONLY=environments/dc
# or for a real init/plan against local code
cd deploy/terraform/environments/dc/1-core
envsubst < local_override.tf.tpl > local_override.tf
```

The `//` in `${DIR}//deploy/terraform/stacks/...` makes the repository root the
module package so the stacks' relative `../../modules/...` references resolve.

## Apply order

Prerequisites: Terraform >= 1.9; `az login` as a DC operator identity with
rights to create resources and role assignments in the subscription; your egress
IP in `key_vault_allowed_ip_ranges`; a public DNS zone for the gateway
hostname(s).

1. **`1-core/`** - `terraform init && terraform apply`. Record
   `kms_gateway_stack_inputs`, `cs_gateway_stack_inputs`, ledger names,
   endpoints and TLS certificates.
2. **Manual: ledger Private Link Service** for the KMS ledger (and the CS ledger
   if its gateway is enabled). ARM/Terraform do not expose the ACL PLS: create it
   out of band (portal *Networking* blade / ACL team) and record the alias
   (`<ledger>-pls.<guid>.<region>.azure.privatelinkservice`).
3. **`2-gateway/`** - fill `kms_core` (and optionally `cs_core`) and the PLS
   aliases in `terraform.tfvars`, then `terraform init && terraform apply`.
4. **Manual: approve** every connection in `ledger_private_endpoint_ids` on the
   corresponding PLS (*Private endpoint connections*).
5. **DNS:** create the A records in `dns_records`. Pin the KMS public IP across
   rebuilds via the stack's `gateway_public_ip_id`.
6. **TLS:** configure the KMS repo GitHub environment from
   `renewal_workflow_settings.kms` (+ `AZURE_CLIENT_ID` =
   `kms_ops_identity_client_id`, tenant, subscription, secret `LETSENCRYPT_EMAIL`)
   and run *Refresh frontend certificate* (staging first, then production).
7. **Applications** (service tooling, not Terraform), for each ledger:
   `PUT <ledger_endpoint>/app/userDefinedEndpoints?api-version=2024-08-22-preview`
   with an ACL Administrator AAD token; `POST /app/gov/bootstrap` once (spec
   §3.3); `approve_app_digest` with the released bundle digest; pin peers with
   `set_peer_service` in both directions (spec §5); in the KMS,
   `set_oidc_settings.issuer_url = kms_oidc_issuer_url`
   (`https://<kms_gateway_hostname>/app/oidc`).
8. **Publish to parties:** `kms_oidc_issuer_url` (TDP/TDC storage federation
   trust), both ledger endpoints and TLS certificates (`DEPA_KMS_URL`,
   `DEPA_CS_URL`, `DEPA_TRUST_BUNDLE`).

Destroy in reverse order (`2-gateway`, then `1-core`).
