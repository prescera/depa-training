# Environment: single-subscription Azure deployment (whole collaborative in one subscription)

One subscription, one resource group per party, parameterised by `parties`. The workflows use
the fixed demo set from `deploy/scripts/demo-parties.py` (`dc-member-1`, `tdc` [tdc, tdp],
`tdp-1` … `tdp-4`), onto which the SDK maps any scenario's providers. Multi-subscription / multi-cloud
deployments use the per-role environments (`dc`, `tdp-*`, `tdc-*`) instead.

| Phase | Creates | Needs |
|---|---|---|
| [`1-platform`](1-platform) | `<prefix>-<party>-rg` per party, `<prefix>-dc-kms-rg` / `<prefix>-dc-cs-rg` (KMS and CS stacks: VNet, Key Vault + member cert, ACL ledger), `<prefix>-shared-rg` (ACR, Log Analytics), the TDC's AKS + virtual nodes infra in its group | DC service repos (pinned git sources or `local_override.tf.tpl`) |
| — | `deploy/scripts/deploy-services.sh` (bundles → ACL) and `depa dc setup` (bootstrap creates the KMS OIDC key) | phase 1 |
| [`2-workloads`](2-workloads) | per TDP (incl. `tdc`): dataset storage (one `datasets` container, broker grant `dataset:<party>/*`) + federation broker; per TDC: output storage + broker; virtual nodes Helm release, `depa-ccr` namespace, quota, submitter RBAC | `platform` = phase-1 output `platform`, pinned KMS JWKS, broker package, `kubelogin` |

No public KMS gateway is required: the brokers pin the KMS JWKS and the issuer is the KMS ledger
endpoint (`…/app/oidc`). The exact click-by-click and command-line procedures, required roles,
variables and secrets are in [`deploy/README.md`](../../../README.md).
