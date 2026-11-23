# Production deployment

In production, each party deploys only its own plane, in its own cloud account, and runs only
its own steps with its own keys.

| Role | Deploys (Terraform) | Runs (CLI) |
|---|---|---|
| **DC operator** | [`environments/dc`](../../deploy/terraform/environments/dc): KMS and CS on ACL (`1-core`), public gateway for the OIDC issuer (`2-gateway`). Service repos: `deploy/terraform`. | `scripts/deploy-acl.sh` in each service repo (reproducible bundle), `depa dc bootstrap` once |
| **DC members** | — | `depa dc proposals`, `dc vote`, `dc release approve` after reviewing a release's reproducible build, `dc audit` |
| **TDP** | [`tdp-azure`](../../deploy/terraform/environments/tdp-azure) / [`tdp-aws`](../../deploy/terraform/environments/tdp-aws) / [`tdp-gcp`](../../deploy/terraform/environments/tdp-gcp): storage and federation trust for `dataset:<id>` | `depa party keygen`, `dataset create-key`, `dataset policy set`, `dataset encrypt/upload/publish`, `contract verify/sign`, `dataset suspend` (kill switch) |
| **TDC** | [`tdc-azure-aks`](../../deploy/terraform/environments/tdc-azure-aks) or [`tdc-gcp-cs`](../../deploy/terraform/environments/tdc-gcp-cs): compute, output storage | `contract propose`, `job submit`, `run verify-receipt`, `model fetch/decrypt` |
| **Clean-room provider** | Same compute environments as a TDC, run on the TDC's behalf | `job submit` |

## Order of operations

1. **DC:** deploy `1-core`, then (with the ACL team) the ledger Private Link Service, then
   `2-gateway`. Deploy both bundles. Bootstrap with the founding members. Members then
   register parties, pin peers, set `trust_bundle_digest` and approve clean-room releases.
2. **Parties:** generate keys (`depa party keygen`). The DC registers each party by vote.
3. **TDPs:** deploy storage with the KMS issuer, then create dataset keys and policies, encrypt,
   upload and publish.
4. **TDC:** deploy compute. Propose a contract. Each TDP verifies and signs.
5. **Run:** `depa job submit --platform aks|gcp`. Verify receipts and fetch the model.

## Production checklist

- **Keys:** keep party signing keys in an HSM or Key Vault behind the SDK's `Signer` interface.
  Never share them.
- **DC operator:** hold the ACL Administrator role under a multi-approver process (threat model
  R1). Approve only reproducible bundle digests (`approve_app_digest`) and run `depa dc audit`
  continuously.
- **Gateway:** treat the OIDC gateway's configuration as security-critical. Monitor its JWKS
  against `GET /info` (threat model R2).
- **Releases:** use production releases only (stdio disabled). Keep the UVM minimum SVN current.
  It is 104 as of August 2026 per Microsoft's
  [Confidential ACI scheme](https://github.com/microsoft/confidential-aci-examples/blob/main/docs/Confidential_ACI_SCHEME.md),
  and the infrastructure fragment minimum is 4. Revoke releases on security advisories.
- **TDP policies:** never list `virtual`. List only the platforms, tiers and consumers you
  intend. Set a DP floor where needed.
- **ACL team:** ask them to register the Google Cloud Attestation issuer (for GCP clean rooms),
  and to provide RS256 support (to retire the Azure SAS broker) and multi-party approval for
  bundle updates.
