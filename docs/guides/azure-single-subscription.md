# Single-subscription Azure deployment

This demo deployment puts a whole Data Collaborative into **one Azure subscription**, with a separate
resource group per party, and runs scenarios end to end from GitHub Actions. It uses the same
components as production. The only shortcut is that one operator holds every party's keys,
which is acceptable for synthetic or demo data only.

| Resource group | Party | Contents |
|---|---|---|
| DC | Data Collaborative | KMS and Contract Service (each on its own Azure Confidential Ledger), Key Vaults |
| one per provider (`tdc`, `tdp-1`…`tdp-4`) | Data providers | Storage account with a `datasets` container, plus a SAS federation broker |
| TDC | Consumer | Output storage and broker, AKS with virtual nodes v2 for confidential ACI, ACR |

## 1. One-time setup (about 15 minutes)

Follow [deploy/README.md → one-time setup](../../deploy/README.md#single-subscription-azure-deployment-one-time-setup). It:

1. registers resource providers;
2. creates an app registration whose **federated credential** trusts only this repository's
   GitHub environment, so no client secret exists anywhere;
3. grants it Contributor and User Access Administrator on a dedicated subscription;
4. creates an AKS admin group and a Terraform state account;
5. stores the Azure identifiers (client, tenant and subscription ids, AKS admin group,
   deployer object id, Terraform state group and account) as GitHub **environment secrets**,
   and the region, region short code, name prefix and `DEPA_SERVICES_REF` as environment
   **variables**;
6. generates keys for the six fixed demo parties and publishes them as the `DEPA_PARTY_KEYS`
   secret (`deploy/scripts/party-keys.sh init` + `publish`).

The deployment always has the same parties: `dc-member-1`, `tdc` (also a provider: it owns the
llm-sft base model) and `tdp-1` … `tdp-4`. Every scenario's own provider ids are mapped onto
these slots, so one deployment runs any scenario.

The default region is **`centralindia`** (short code `cin`). The region must offer Azure
Confidential Ledger, Functions Flex Consumption and AKS virtual nodes v2 with confidential pods.
Workflow **1 · Platform** checks all three before it plans anything
(`deploy/scripts/check-region.sh`), and when a region fails it lists the regions that offer all
three. Add required reviewers to the environment to get an approval gate.

## 2. Deploy (Actions tab)

One click: **End to end: steps 1–5 for one scenario** (environment, scenario, platform, optional
teardown). The workflows sort in run order and can also be run one by one:

| Step | Workflow | Result |
|---|---|---|
| 1 | 1 · Platform: ledgers for KMS and contract service, container registry, AKS cluster (Azure) | Party resource groups, both ledgers with Key Vaults, ACR, AKS |
| 2 | 2 · DC services: KMS and contract service apps on Azure Confidential Ledger, governance setup | Both bundles on their ledgers, governance set up |
| 3 | 3 · Party resources: provider and consumer storage with SAS brokers, AKS virtual nodes for clean rooms | Provider storage and SAS brokers (KMS keys pinned, broker code deployed), consumer output storage, virtual nodes |
| 4 | 4 · Clean-room images: build, push to ACR, CCE policies, release manifests | Clean-room images in ACR, CCE policies, release manifests |
| 5 | 5 · Run scenario: dataset keys and policies → encrypt and upload data → contract signing → clean-room training → model download | One step per stage and party role; uploads the stage summaries and receipts |

Re-run step 5 for any scenario; no redeployment is needed. **Tear down all Azure resources**
removes everything.

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
