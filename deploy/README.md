# Deploying DEPA Training v2

| Path | What |
|---|---|
| [`terraform/`](terraform) | Modules and per-role environments (multi-subscription, multi-cloud production layout). |
| [`terraform/environments/azure-pilot`](terraform/environments/azure-pilot) | One Azure subscription, one resource group per party: DC services (KMS + CS on ACL), each TDP's storage + SAS broker, TDC output storage, TDC AKS + virtual nodes v2 cluster, ACR. |
| [`scripts/`](scripts) | `pilot-parties.py` (the fixed pilot parties), `deploy-services.sh` (KMS/CS bundles → ACL), `fetch-kms-jwks.sh` (KMS JWKS to pin in brokers), `build-releases.sh` (CCR images → ACR, CCE policies, release manifests), `render-env.sh` (Terraform outputs → `depa-env.yaml`), `party-keys.sh` (party keys ↔ GitHub secret). |
| `../.github/workflows/` | `deploy-infra.yml`, `deploy-services.yml`, `build-releases.yml`, `run-scenario.yml`, `teardown.yml` (each `workflow_dispatch` + `workflow_call`, Azure login with GitHub OIDC, gated by a GitHub environment); `demo.yml` chains them end to end; `pr-demo.yml` runs it for PRs labelled `run-azure-demo`; `e2e-local.yml` is the always-on sandbox check. |

## Azure pilot: one-time setup

Everything below is run once by a subscription Owner with `az` and `gh` (GitHub CLI) logged in.
No client secret is ever created: GitHub Actions authenticates with a federated credential.

```bash
SUB=<subscription-id>
REPO=prescera/depa-training          # this repository; the service repos are checked out from the same owner
ENV=pilot                             # GitHub environment name (also the state key prefix)
LOC=eastus                            # pilot default; production India: centralindia (checked by deploy-infra preflight)
STATE_SA=<globally-unique-name>       # Terraform state storage account (3-24 lowercase alphanumerics)

az login && az account set --subscription "$SUB"
TENANT=$(az account show --query tenantId -o tsv)

# 1. Resource providers
for p in Microsoft.ConfidentialLedger Microsoft.ContainerService Microsoft.ContainerInstance \
         Microsoft.ContainerRegistry Microsoft.KeyVault Microsoft.Web Microsoft.Storage \
         Microsoft.OperationalInsights Microsoft.Insights Microsoft.ManagedIdentity Microsoft.Network; do
  az provider register --namespace "$p"
done

# 2. Deployment identity: app registration + service principal, trusted for this repo's
#    GitHub environment only (subject repo:<owner>/<repo>:environment:<env>)
APP_ID=$(az ad app create --display-name "depa-training-v2-$ENV" --query appId -o tsv)
SP_OID=$(az ad sp create --id "$APP_ID" --query id -o tsv)
az ad app federated-credential create --id "$APP_ID" --parameters "{
  \"name\": \"github-$ENV\",
  \"issuer\": \"https://token.actions.githubusercontent.com\",
  \"subject\": \"repo:$REPO:environment:$ENV\",
  \"audiences\": [\"api://AzureADTokenExchange\"]
}"

# 3. Roles. Terraform creates resource groups, role assignments and one custom role definition
#    (write-only output role), hence Contributor + User Access Administrator. Use a dedicated
#    subscription for the pilot.
az role assignment create --assignee-object-id "$SP_OID" --assignee-principal-type ServicePrincipal \
  --role Contributor --scope "/subscriptions/$SUB"
az role assignment create --assignee-object-id "$SP_OID" --assignee-principal-type ServicePrincipal \
  --role "User Access Administrator" --scope "/subscriptions/$SUB"

# 4. AKS admin group (local accounts are disabled on the clean-room cluster)
GROUP_OID=$(az ad group create --display-name "depa-training-v2-aks-admins-$ENV" \
  --mail-nickname "depa-training-v2-aks-admins-$ENV" --query id -o tsv)
az ad group member add --group "$GROUP_OID" --member-id "$SP_OID"

# 5. Terraform state (Entra-only, no shared keys)
az group create --name "depa-training-v2-tfstate-rg" --location "$LOC"
az storage account create --name "$STATE_SA" --resource-group depa-training-v2-tfstate-rg --location "$LOC" \
  --sku Standard_ZRS --min-tls-version TLS1_2 --allow-blob-public-access false \
  --allow-shared-key-access false
STATE_ID=$(az storage account show --name "$STATE_SA" --resource-group depa-training-v2-tfstate-rg --query id -o tsv)
az role assignment create --assignee-object-id "$SP_OID" --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Contributor" --scope "$STATE_ID"
az role assignment create --assignee "$(az ad signed-in-user show --query id -o tsv)" \
  --role "Storage Blob Data Contributor" --scope "$STATE_ID"
az storage container create --name tfstate --account-name "$STATE_SA" --auth-mode login

# 6. GitHub environment (add required reviewers in Settings → Environments for an approval gate).
#    Azure identifiers are environment *secrets*; region and naming are environment *variables*.
gh api --method PUT "repos/$REPO/environments/$ENV" >/dev/null
s() { gh secret set "$1" --env "$ENV" --repo "$REPO" --body "$2"; }
v() { gh variable set "$1" --env "$ENV" --repo "$REPO" --body "$2"; }
s AZURE_CLIENT_ID "$APP_ID"
s AZURE_TENANT_ID "$TENANT"
s AZURE_SUBSCRIPTION_ID "$SUB"
s AKS_ADMIN_GROUP_OBJECT_ID "$GROUP_OID"
s DEPLOYER_OBJECT_ID "$SP_OID"
s TFSTATE_RESOURCE_GROUP depa-training-v2-tfstate-rg
s TFSTATE_STORAGE_ACCOUNT "$STATE_SA"
v AZURE_LOCATION "$LOC"
v AZURE_REGION_SHORT eus                     # 2-6 chars, used in ledger names (cin for centralindia)
v PILOT_NAME_PREFIX pilot1                   # 2-8 chars
v DEPA_SERVICES_REF main                     # ref of <owner>/depa-training-kms-azure and <owner>/contract-service
# Optional repository variable for PRs labelled run-azure-demo (default: pilot):
# gh variable set DEMO_ENVIRONMENT --repo "$REPO" --body "$ENV"
```

### Party keys (secret `DEPA_PARTY_KEYS`)

The pilot always has the same six parties, whatever scenario runs: `dc-member-1`
(dc-member), `tdc` (tdc + tdp: it owns the llm-sft base model) and `tdp-1` … `tdp-4` (tdp; four
is the most providers in any scenario). The SDK maps each scenario's own provider ids onto these
slots. The workflows act for every party, so they need all six key directories. With the SDK
installed (`pip install ./sdk/python` from the repository root):

```bash
deploy/scripts/party-keys.sh init ./pilot-keys                          # refuses to overwrite existing keys
deploy/scripts/party-keys.sh publish ./pilot-keys --env "$ENV" --repo "$REPO"
```

`publish` packs into a private temporary file and only sets the secret when packing succeeded,
so a missing or incomplete key directory can never produce an empty secret. Keep `./pilot-keys`
somewhere safe (it is needed to re-publish) or regenerate and re-publish before a fresh pilot.

This concentrates every party's signing key in one GitHub environment: acceptable for a pilot
on synthetic data only. Production parties keep their own keys and run their own steps.

### Optional: GCP clean rooms (`platform = gcp` in run-scenario)

Deploy [`environments/tdc-gcp-cs`](terraform/environments/tdc-gcp-cs) and set `GCP_PROJECT`,
`GCP_ZONE`, `GCP_SERVICE_ACCOUNT` (workload SA), `GCP_SUBNET`, `GCP_WORKLOAD_IDENTITY_PROVIDER`
and `GCP_SUBMITTER_SERVICE_ACCOUNT` (a job-submitter member, impersonated via Workload
Identity Federation from this repository) as environment variables.

## Azure pilot: deploy and run (Actions tab, choose the environment each time)

**One click:** *Pilot · end-to-end demo* (inputs: environment, scenario, platform, teardown)
runs the stages below as reusable workflows (releases are built in parallel with the services
and workloads stages; teardown runs even after a failure when selected). Label a pull request
`run-azure-demo` to run it for credit-risk with teardown in the environment named by the
repository variable `DEMO_ENVIRONMENT` (default `pilot`; same-repository PRs only). Stage by
stage:

| # | Workflow | Inputs | Does |
|---|---|---|---|
| 1 | **Pilot · deploy infrastructure** | phase `1-platform`, apply ✓ | region preflight (ACL, Flex Consumption, VN2 confidential pods), party resource groups, KMS + CS ledgers (Key Vaults, member certs), ACR, AKS + virtual nodes infra, Log Analytics |
| 2 | **Pilot · deploy services** | — | builds both bundles, `PUT /app/userDefinedEndpoints` on each ledger, `depa dc setup` (bootstrap → KMS OIDC key, members, settings) |
| 3 | **Pilot · deploy infrastructure** | phase `2-workloads`, apply ✓ | per TDP: storage + federation broker (JWKS pinned from step 2); per TDC: output storage + broker; virtual nodes Helm release, namespace, RBAC |
| 4 | **Pilot · build CCR releases** | version, families | images → ACR, CCE policies, release manifests (artifact `ccr-releases-<env>`) |
| 5 | **Pilot · run scenario** | scenario, platform | `depa-env.yaml`, keys, `depa dc setup` (idempotent), `depa scenario deploy`; uploads receipts + run summary |
| — | **Pilot · teardown** | confirm | destroys phase 2 then phase 1 |

Any scenario runs on the same deployment: pick it in step 5 (or in the demo).

### Why the pilot needs no public KMS gateway

The KMS signs ES256 OIDC tokens. Azure Blob access goes through each TDP's
[federation broker](terraform/modules/azure/federation-broker) (spec §10.1), which pins the KMS
JWKS (fetched over TLS pinned to the KMS ledger identity and cross-checked with `GET /info`), so
no publicly trusted issuer endpoint is involved and the issuer is the ledger URL
(`https://<kms-ledger>.confidential-ledger.azure.com/app/oidc`). Datasets on AWS/GCP need
the public issuer: add [`environments/dc/2-gateway`](terraform/environments/dc) using the
`kms_gateway_stack_inputs` output of `1-platform`.

### Running locally instead of from Actions

```bash
cd deploy/terraform/environments/azure-pilot/1-platform
cp terraform.tfvars.example terraform.tfvars            # or: ../../../../scripts/pilot-parties.py mnist > parties.auto.tfvars.json
terraform init && terraform apply                       # DC stacks from github.com/prescera/*@main (or local_override.tf.tpl for checkouts)
terraform output -json pilot_platform > /tmp/platform.json
../../../../scripts/deploy-services.sh --platform-json /tmp/platform.json --kms-repo <kms checkout> --cs-repo <cs checkout>
# depa dc setup …, then:
cd ../2-workloads
jq '{platform: .}' /tmp/platform.json > platform.auto.tfvars.json
# set broker_package_path (federation-broker/build-package.sh) and
# kms_jwks_json ($(deploy/scripts/fetch-kms-jwks.sh <kms-ledger>)) in terraform.tfvars
terraform init && terraform apply
deploy/scripts/render-env.sh --parties-json parties.json --keys-dir ./pilot-keys --releases-dir ccr/releases --out depa-env.yaml
```

## Troubleshooting

- **`DEPA_PARTY_KEYS is empty`** in *deploy services* or *run scenario*: the secret was set from
  an empty value (e.g. an earlier `gh secret set --body "$(…)"` whose command failed). Re-publish
  it with `party-keys.sh init` (only if you have no keys yet) and `party-keys.sh publish` as
  above. **The current `pilot` environment's secret is empty and must be re-published.**
- **`<NAME> is empty: set the environment secret/variable`**: an Azure identifier secret or a
  region/naming variable is missing from the GitHub environment (step 6).
- **Region preflight fails**: the region lacks ACL, Flex Consumption or virtual nodes v2
  confidential pods; pick another (`eastus`, `centralindia`, …) and update the variables.

## Assumptions and limits

- `depa dc setup` is idempotent and approves the CCR releases found in `releases_dir`;
  `depa --json scenario deploy` prints the run summary (uploaded with any `*receipt*` files from
  its `--work-dir`; the work dir itself is never uploaded because it holds plaintext data)
  (CLI owned by the SDK).
- `depa-env.yaml` follows `sdk/python/src/depa/env/schema.json`: `service_cert` is a path
  relative to the env file (`certs/kms.pem`, `certs/cs.pem`); each pilot TDP keeps all its
  datasets in one `datasets` container, so `storage.<party>.uri` is that container and the
  SDK's `<uri><dataset name>/<version>/` lands there. Its broker grants `dataset:<party>/*`
  (any dataset of that owner, ciphertext only: keys are still released per dataset by the KMS).
- Pilot only: the deployment principal gets *Storage Blob Data Contributor* on TDP data accounts
  and *Storage Blob Data Reader* on TDC output accounts (`operator_object_ids`) because the
  workflows act for every party; clean rooms themselves only ever use broker-issued SAS.
- Egress from clean rooms is allowed to `AzureCloud.<region>` on 443 because ACL has no service
  tag (defence in depth only; the CCR is the boundary).
- ACL user-defined endpoints (custom JS apps) must be available in the chosen region.
