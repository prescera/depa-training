# azure/federation-broker

Interim Azure Blob federation (protocol spec §10.1). The DC KMS signs its OIDC tokens with
**ES256** (CCF 6 has no RSASSA-PKCS1-v1_5), which Microsoft Entra federated credentials do not
accept. Until it can issue RS256, an Azure-hosted TDP (or TDC, for outputs) runs this broker in
its **own** subscription: no third party joins the trust chain.

```
CCR ──POST /sas, Bearer <KMS token (aud = broker audience)>──▶ broker (Azure Function)
     ◀── 200 {"sas_url": "https://<acct>.blob.core.windows.net/<container>?<sas>",
              "prefix": "<prefix>", "expires_at": "<RFC3339>"}
```

The broker verifies the token against the **pinned** KMS JWKS (ES256 only, `kid` lookup, raw
r‖s signature), `iss` = `oidc_issuer_url`, `aud` = `audience`, `exp`/`nbf`/`iat` with 30 s
leeway, lifetime ≤ 1 h, and `sub` ∈ configured grants (exact, or an owner wildcard
`dataset:<owner>/*` matching `dataset:<owner>/<name>` for a container that holds all of that
owner's datasets, as in the Azure pilot). It answers **401** for any token
problem (no detail), **403** for a valid token whose `sub` is not configured, otherwise a
**user-delegation SAS** on the grant's container: `sp=rl` for `dataset:<id>`, `sp=cw` for
`output:<party>`, `spr=https`, expiry = min(token `exp`, now + `sas_max_ttl_seconds`). Every
issuance is logged (sub, jti, contract_id, run_id, measurement_id, container, expiry) to
Application Insights.

## Provisions

- Signer identity: *Storage Blob Delegator* on the data account + *Storage Blob Data Reader*
  (read grants) or the write-only custom role (write grants) **per container**. A
  user-delegation SAS can never exceed its signer's RBAC, so these roles bound every SAS.
- Host identity: *Storage Blob Data Owner* on a dedicated host/deployment storage account
  (shared keys disabled) and *Monitoring Metrics Publisher* on Application Insights (local
  auth disabled). No data access.
- Flex Consumption plan (FC1) + Linux Function app, Python 3.12, HTTPS only, TLS 1.2, basic
  publishing auth disabled, code deployed from `package_path`.
- Log Analytics workspace (unless one is passed) + workspace-based Application Insights.

## Inputs (main)

`name_prefix`, `resource_group_name`, `location`, `storage_account_id`/`_name`,
`blob_endpoint`, `grants` (`{sub => {access, container, container_id, prefix}}`),
`writer_role_definition_id`, `oidc_issuer_url`, `kms_jwks_json` (pinned), `jwks_refresh`
(default false: on an unknown `kid`, refresh only from the issuer's discovery document, at most
every 5 min), `audience` (default `api://depa-federation-broker/<account>`),
`sas_max_ttl_seconds` (default 900), `package_path`, `host_storage_account_name`.

Outputs: `broker_url`, `audience` (descriptor `federation.azure = {broker_url, audience}`;
the audience also goes into the dataset Release Policy `allowed_audiences` or the KMS
`allowed_audiences_global`), `signer_principal_id`, `function_app_name`.

Normally used through [`tdp-storage`](../tdp-storage) / [`tdc-output-storage`](../tdc-output-storage)
(`federation_mode = "broker"`, the default).

## Build and test the function

```bash
./build-package.sh            # deterministic zip with hash-locked deps -> dist/federation-broker-<hash>.zip
cd function && pip install -r requirements.txt -r requirements-test.txt && pytest tests
```

`function/requirements.txt` is hash-locked (`azure-functions`, `cryptography` and their
dependencies); everything else is stdlib (managed-identity token and user delegation key via
REST). The SAS tests check parity with the Azure SDK's implementation for `SAS_VERSION`.

## Operational notes

- **Key rotation**: with `jwks_refresh = false`, re-apply with the new JWKS before the KMS
  starts signing with a new key (governance announces rotations).
- If the data account uses `allowed_ip_rules`, the broker reaches it via the public endpoint
  from Flex Consumption's dynamic outbound IPs; either leave the account network-open
  (authorization stays Entra/SAS-only) or add VNet integration (not included).
- Switch to native Entra federation (`federation_mode = "entra"`) once the KMS issues RS256;
  clean rooms then only need the updated descriptor.
