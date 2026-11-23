# Azure pilot

The pilot deploys a whole Data Collaborative into **one Azure subscription**, with a separate
resource group per party, and runs scenarios end to end from GitHub Actions. It uses the same
components as production. The only shortcut is that one operator holds every party's keys,
which is acceptable for synthetic or demo data only.

| Resource group | Party | Contents |
|---|---|---|
| DC | Data Collaborative | KMS and Contract Service (each on its own Azure Confidential Ledger), Key Vaults |
| one per TDP | Data providers | Storage account with per-dataset containers, plus a SAS federation broker |
| TDC | Consumer | Output storage and broker, AKS with virtual nodes v2 for confidential ACI, ACR |

## 1. One-time setup (about 15 minutes)

Follow [deploy/README.md → one-time setup](../../deploy/README.md#azure-pilot-one-time-setup). It:

1. registers resource providers;
2. creates an app registration whose **federated credential** trusts only this repository's
   GitHub environment, so no client secret exists anywhere;
3. grants it Contributor and User Access Administrator on a dedicated subscription;
4. creates an AKS admin group and a Terraform state account;
5. sets the GitHub environment variables (region, name prefix, `PILOT_SCENARIOS`);
6. stores the pilot party keys in the `DEPA_PARTY_KEYS` secret.

The pilot default region is **`eastus`** (short code `eus`); production deployments in India
use `centralindia` (`cin`). The region must offer Azure Confidential Ledger, Functions Flex
Consumption and AKS virtual nodes v2 with confidential pods; **Pilot · deploy infrastructure**
checks all three before it plans anything (`deploy/scripts/check-region.sh`). Add required
reviewers to the environment to get an approval gate.

## 2. Deploy (Actions tab)

One click: **Pilot · end-to-end demo** (environment, scenario, platform, optional teardown)
runs the five steps below in order. They can also be run one by one:

| Step | Workflow | Result |
|---|---|---|
| 1 | Pilot · deploy infrastructure, phase `1-platform` | Party resource groups, ledgers, ACR, AKS |
| 2 | Pilot · deploy services | KMS and CS bundles on ACL, governance set up |
| 3 | Pilot · deploy infrastructure, phase `2-workloads` | TDP storage and brokers (KMS keys pinned), TDC output storage, virtual nodes |
| 4 | Pilot · build CCR releases | Clean-room images in ACR, CCE policies, release manifests |
| 5 | Pilot · run scenario | Pick a scenario and the `aks` platform. Uploads the run summary and receipts. |

Re-run step 5 for any scenario listed in `PILOT_SCENARIOS`. To add a scenario, extend the
variable and the key secret, then re-run steps 1, 3 and 5. **Pilot · teardown** removes
everything.

## 3. Check the result

The run summary shows the contract, run id, metrics, privacy spent and output digests. The
receipt files can be verified independently with
`depa --env depa-env.yaml run verify-receipt <run_id> --txid <txid>`.

## Before you start: confirm with Azure

- ACL user-defined endpoints (custom JS apps) are available in your region (the preflight checks
  ACL, Flex Consumption and virtual nodes v2 confidential pods, not this preview feature).
- Release manifests require UVM SVN ≥ 104, the current production value per
  [Confidential_ACI_SCHEME.md](https://github.com/microsoft/confidential-aci-examples/blob/main/docs/Confidential_ACI_SCHEME.md);
  raise `--uvm-min-svn` when Microsoft publishes a newer UVM.

GCP clean rooms (`platform = gcp`) additionally need the Google Cloud Attestation issuer
registered on the KMS ledger by the ACL team, and the `tdc-gcp-cs` environment. Datasets on AWS or GCP
need the public KMS issuer gateway (`environments/dc/2-gateway`).
