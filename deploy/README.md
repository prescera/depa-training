# Deploying DEPA Training v2

| Path | What |
|---|---|
| [`terraform/`](terraform) | Modules and per-role environments (multi-subscription, multi-cloud production layout). |
| [`terraform/environments/azure-single-subscription`](terraform/environments/azure-single-subscription) | One Azure subscription, one resource group per party, one Terraform root and state per component: `kms`, `contract-service`, `registry`, `data-provider` (×4), `data-consumer`, `ccr-provider-cluster`, `ccr-provider-config`. |
| [`scripts/`](scripts) | `demo-parties.py` (the fixed demo parties), `deploy-brokers.sh` (SAS broker code → function apps, Entra auth), `deploy-services.sh` (KMS/CS bundles → ACL), `fetch-kms-jwks.sh` (KMS JWKS to pin in brokers), `build-releases.sh` (CCR images → ACR, CCE policies, release manifests), `state-outputs.sh` (a component's outputs from its state blob), `render-env.sh` (all component outputs → `depa-env.yaml`), `party-keys.sh` (party keys ↔ GitHub secret). |
| `../.github/workflows/` | The workflow tree below: every workflow is `workflow_dispatch` + `workflow_call` (runnable alone), logs in to Azure with GitHub OIDC and runs in a GitHub environment; shared logic in the composite actions `azure-terraform` and `depa-cli`. **Local end-to-end** is the always-on sandbox check. |

## Single-subscription Azure deployment: one-time setup

Everything below is run once by a subscription Owner with `az` and `gh` (GitHub CLI) logged in.
No client secret is ever created: GitHub Actions authenticates with a federated credential.

```bash
SUB=<subscription-id>
REPO=prescera/depa-training          # this repository; the service repos are checked out from the same owner
ENV=<environment>                     # GitHub environment name (also the state key prefix), e.g. demo
LOC=centralindia                      # offers ACL, VN2 confidential pods and Flex Consumption (checked by "Deploy KMS infra")
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
#    GitHub environment only. The subject must match exactly what GitHub issues: repositories
#    with immutable subjects use repo:<owner>@<owner_id>/<repo>@<repo_id>, so read the prefix
#    from GitHub instead of assuming repo:<owner>/<repo>.
SUB_PREFIX=$(gh api "repos/$REPO/actions/oidc/customization/sub" --jq ".sub_claim_prefix // empty")
SUB_PREFIX=${SUB_PREFIX:-repo:$REPO}
APP_ID=$(az ad app create --display-name "depa-training-v2-$ENV" --query appId -o tsv)
SP_OID=$(az ad sp create --id "$APP_ID" --query id -o tsv)
az ad app federated-credential create --id "$APP_ID" --parameters "{
  \"name\": \"github-$ENV\",
  \"issuer\": \"https://token.actions.githubusercontent.com\",
  \"subject\": \"$SUB_PREFIX:environment:$ENV\",
  \"audiences\": [\"api://AzureADTokenExchange\"]
}"

# 3. Roles. Terraform creates resource groups, role assignments and one custom role definition
#    (write-only output role), hence Contributor + User Access Administrator. Use a dedicated
#    subscription for this deployment.
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
v AZURE_REGION_SHORT cin                     # 2-6 chars, used in ledger names
v NAME_PREFIX demo1                   # 2-8 chars
v DEPA_SERVICES_REF main                     # ref of <owner>/depa-training-kms-azure and <owner>/contract-service
# Repository variable naming the environment for PRs labelled run-azure-demo (required for that workflow):
# gh variable set DEMO_ENVIRONMENT --repo "$REPO" --body "$ENV"
```

### Party keys (secret `DEPA_PARTY_KEYS`)

The demo deployment always has the same six parties, whatever scenario runs: `dc-member-1`
(dc-member), `tdc` (tdc + tdp: it owns the llm-sft base model) and `tdp-1` … `tdp-4` (tdp; four
is the most providers in any scenario). The SDK maps each scenario's own provider ids onto these
slots. The workflows act for every party, so they need all six key directories. With the SDK
installed (`pip install ./sdk/python` from the repository root):

```bash
deploy/scripts/party-keys.sh init ./demo-keys                          # refuses to overwrite existing keys
deploy/scripts/party-keys.sh publish ./demo-keys --env "$ENV" --repo "$REPO"
```

`publish` packs into a private temporary file and only sets the secret when packing succeeded,
so a missing or incomplete key directory can never produce an empty secret. Keep `./demo-keys`
somewhere safe (it is needed to re-publish) or regenerate and re-publish before a fresh deployment.

This concentrates every party's signing key in one GitHub environment: acceptable for a demo
on synthetic data only. Production parties keep their own keys and run their own steps.

### Optional: GCP clean rooms (`platform = gcp` in "Run Scenario")

Deploy [`environments/tdc-gcp-cs`](terraform/environments/tdc-gcp-cs) and set `GCP_PROJECT`,
`GCP_ZONE`, `GCP_SERVICE_ACCOUNT` (workload SA), `GCP_SUBNET`, `GCP_WORKLOAD_IDENTITY_PROVIDER`
and `GCP_SUBMITTER_SERVICE_ACCOUNT` (a job-submitter member, impersonated via Workload
Identity Federation from this repository) as environment variables.

## Single-subscription Azure deployment: deploy and run (Actions tab, choose the environment each time)

**One click:** **End-to-End demo** (inputs: environment, scenario, platform,
skip_cleanroom_build, teardown). Label a pull request `run-azure-demo` to run it for credit-risk
with teardown in the environment named by the repository variable `DEMO_ENVIRONMENT`
(same-repository PRs only). The tree mirrors the DEPA Training flow; every node is its own
workflow and can be run alone:

```
End-to-End demo                              end-to-end.yml
├─ Setup Data Collab                         setup-data-collab.yml
│  ├─ Deploy Collab infra                    deploy-collab-infra.yml
│  │  ├─ Deploy KMS infra                    deploy-kms-infra.yml            region check, root kms
│  │  └─ Deploy Contract Service infra       deploy-contract-service-infra.yml  root contract-service
│  ├─ Bootstrap Trust Services               bootstrap-trust-services.yml
│  │  ├─ Bootstrap KMS                       bootstrap-kms.yml               bundle → ledger, dc setup --step bootstrap-kms
│  │  ├─ Bootstrap Contract Service          bootstrap-contract-service.yml  bundle → ledger, dc setup --step bootstrap-cs
│  │  └─ Link services (job)                 dc setup --step link
│  └─ Cleanroom build and release (skip)     cleanroom-build-release.yml     root registry
│     └─ Cleanroom image ×{classic,torch,llm} cleanroom-image.yml           image → ACR, CCE policy, manifests
├─ Setup Training Collaborators              setup-training-collaborators.yml
│  ├─ Setup Data Provider ×{tdp-1..tdp-4}    setup-data-provider.yml         root data-provider, broker code
│  ├─ Setup Data Consumer                    setup-data-consumer.yml         root data-consumer, broker code
│  └─ Setup CCR Provider                     setup-ccr-provider.yml          roots ccr-provider-cluster → ccr-provider-config
├─ Run Scenario                              run-scenario.yml
│  ├─ Preflight (job)                        collaborator + DC component states exist, cluster power state
│  ├─ Resume CCR Provider (resume_ccr_provider) resume-ccr-provider.yml     az aks start, wait for the virtual node
│  ├─ Register collaborators and artefacts   register-collaborators.yml      dc setup --step register
│  ├─ Keys and policies                      keys-and-policies.yml
│  │  ├─ Configure Key release policies      configure-key-release-policies.yml  --stage keys
│  │  └─ Data/Model encryption and storage   data-encryption-storage.yml     --stage data (with the trainer stack)
│  ├─ Contract Signing                       contract-signing.yml            --stage contract
│  ├─ Model Training in CCR                  model-training.yml              --stage train
│  ├─ Model download and provenance          model-download.yml              --stage model, receipts artifact
│  └─ Pause CCR Provider (pause_ccr_provider_after) pause-ccr-provider.yml  az aks stop
└─ Tear down                                 teardown.yml                    scope: scenario | collaborators | all
   ├─ Tear down Scenario                     teardown-scenario.yml           depa scenario teardown: revoke contracts, delete model outputs
   ├─ Tear down Collaborators                teardown-collaborators.yml      data providers, data consumer, CCR config → cluster
   └─ Tear down DC services                  teardown-dc-services.yml        registry (job)
      ├─ Tear down KMS                       teardown-kms.yml
      └─ Tear down Contract Service          teardown-contract-service.yml
```

**Combinations** (End-to-End demo inputs; a first *Plan* job checks the state blobs and fails fast
when a skipped stage's components are missing; teardown also runs after a failed stage, and never
touches collaborators for `scenario` or DC services unless `all`):

| Use | setup_data_collab | setup_collaborators | run_scenario | teardown |
|---|---|---|---|---|
| Fresh demo | ✓ | ✓ | ✓ | `none` |
| Back-to-back scenarios on a running deployment | ✗ | ✗ | ✓ (pick the scenario) | `scenario` (revoke its contracts, delete its outputs) |
| Fresh collaborators, keep DC services | ✗ | ✓ | ✓ | `collaborators` |
| Throwaway run | ✓ | ✓ | ✓ | `all` |
| Collaborators-only teardown | ✗ | ✗ | ✗ | `collaborators` |
| Full teardown | ✗ | ✗ | ✗ | `all` |

Teardown scopes: `scenario` = Tear down Scenario; `collaborators` = Tear down Scenario, then Tear
down Collaborators; `all` = both, then Tear down DC services.
`skip_cleanroom_build` (with `setup_data_collab`) reuses the latest clean-room release manifests.
`resume_ccr_provider` (default on) starts a paused CCR provider cluster before the scenario;
`pause_ccr_provider_after` pauses it afterwards. **Pause CCR Provider** / **Resume CCR Provider**
also run on their own; Setup CCR Provider and Tear down Collaborators start a paused cluster
first (AKS rejects updates on a stopped cluster and the in-cluster config needs the API server).
**Tear down** run on its own takes the same scopes (`scenario` needs a scenario; with
`collaborators`/`all` the scenario is optional). `depa_version`
(the Run Scenario tree; End-to-End demo when called from another workflow) installs the CLI from the GitHub Release
`v<version>` (checksum-verified wheels) instead of building it from the checkout.

Each Terraform leaf initialises one component root (`azure-terraform` action) against
`depa-training/<environment>/<component>.tfstate`. Each scenario stage starts on a fresh runner
(`depa-cli` action: CLI, release manifests, `depa-env.yaml` rendered from the component states,
party keys) with its own `--work-dir`. Clean-room release versions are `2.0.0-demo.<run id>`
because release ids are immutable once approved. Any scenario runs on the same deployment: pick
it in **Run Scenario**.

### Why no public KMS gateway is needed

The KMS signs ES256 OIDC tokens. Azure Blob access goes through each TDP's
[federation broker](terraform/modules/azure/federation-broker) (spec §10.1), which pins the KMS
JWKS (fetched over TLS pinned to the KMS ledger identity and cross-checked with `GET /info`), so
no publicly trusted issuer endpoint is involved and the issuer is the ledger URL
(`https://<kms-ledger>.confidential-ledger.azure.com/app/oidc`). Datasets on AWS/GCP need
the public issuer: add [`environments/dc/2-gateway`](terraform/environments/dc) using the
`gateway_stack_inputs` output of the `kms` root.

### Running locally instead of from Actions

Each root has a `terraform.tfvars.example`; uncomment its backend block (key
`depa-training/<environment>/<component>.tfstate`) or pass `-backend-config`. In order:

```bash
cd deploy/terraform/environments/azure-single-subscription
for c in kms contract-service registry; do (cd $c && terraform init && terraform apply); done   # kms/cs: local_override.tf.tpl for checkouts
S="--account <statestorage> --environment <env>"
../../../scripts/deploy-services.sh --service kms --ledger "$(../../../scripts/state-outputs.sh $S kms | jq -r .ledger_name)" --repo <kms checkout>
../../../scripts/deploy-services.sh --service cs --ledger "$(../../../scripts/state-outputs.sh $S contract-service | jq -r .ledger_name)" --repo <cs checkout>
../../../scripts/render-env.sh $S --parties-json <(../../../scripts/demo-parties.py) --keys-dir ./demo-keys --releases-dir ../../../../ccr/releases --out depa-env.yaml
depa dc setup --env depa-env.yaml --step bootstrap-kms,bootstrap-cs,link
JWKS="$(../../../scripts/fetch-kms-jwks.sh "$(../../../scripts/state-outputs.sh $S kms | jq -r .ledger_name)")"
ISSUER="$(../../../scripts/state-outputs.sh $S kms | jq -r .oidc_issuer_url)"
pkg="$(../../modules/azure/federation-broker/build-package.sh)"
for p in tdp-1 tdp-2 tdp-3 tdp-4; do   # one state per provider: key depa-training/<env>/data-provider-$p.tfstate
  (cd data-provider && terraform init -reconfigure -backend-config=key=depa-training/<env>/data-provider-$p.tfstate \
     && terraform apply -var party_id=$p -var kms_jwks_json="$JWKS" -var kms_oidc_issuer_url="$ISSUER" \
     && ../../../../scripts/deploy-brokers.sh --package "$pkg" --from-json <(terraform output -json brokers))
done
(cd data-consumer && terraform init && terraform apply -var kms_jwks_json="$JWKS" -var kms_oidc_issuer_url="$ISSUER" \
   && ../../../../scripts/deploy-brokers.sh --package "$pkg" --from-json <(terraform output -json brokers))
(cd ccr-provider-cluster && terraform init && terraform apply -var registry_id="$(../../../../scripts/state-outputs.sh $S registry | jq -r .id)")
../../../scripts/state-outputs.sh $S ccr-provider-cluster \
  | jq '{cluster: {resource_group_name, cluster_name, aci_resource_group_name, aci_subnet_name, acr_pull_identity}}' > ccr-provider-config/inputs.auto.tfvars.json
(cd ccr-provider-config && terraform init && terraform apply)   # needs kubelogin
```

## Releases of the Python packages

Tagging `v<version>` runs **Release**: CCR images and manifests per family, plus sdists and wheels
of `depa` (sdk/python), `depa-train` (trainers) and `depa-console` (gui) built with
`python -m build` (each `pyproject.toml` version must equal the tag), with `SHA256SUMS` and build
provenance attestations, all attached to the GitHub Release (a pre-release for tags with `-`).
Publishing to PyPI is optional and off by default: create a PyPI *trusted publisher* for each
project (owner, repository, workflow `release.yml`, environment `pypi`), create the GitHub
environment `pypi`, and set the repository variable `PUBLISH_PYPI=true`.

## Troubleshooting

- **`AADSTS700213: No matching federated identity record found`**: the federated credential's
  subject does not match the token. Compare it with the subject in the error message (or
  `gh api repos/$REPO/actions/oidc/customization/sub`) and add a credential with that exact
  subject plus `:environment:$ENV`.

- **`DEPA_PARTY_KEYS is empty`** in a bootstrap or scenario workflow: the secret was set from
  an empty value (e.g. an earlier `gh secret set --body "$(…)"` whose command failed). Re-publish
  it with `party-keys.sh init` (only if you have no keys yet) and `party-keys.sh publish` as
  above. **If your environment's secret was set before `publish` existed, it may be empty: re-publish it.**
- **`<NAME> is empty: set the environment secret/variable`**: an Azure identifier secret or a
  region/naming variable is missing from the GitHub environment (step 6).
- **Helm: `namespace "vn2" exists and cannot be imported into the current release`** (after an
  earlier failed attempt that created `vn2` outside the chart): "Setup CCR Provider" removes the stray
  namespace before applying, as long as the virtualnode release is not installed. When applying
  by hand, run `az aks command invoke -g <tdc-rg> -n <cluster> --command "kubectl delete namespace vn2"`.
- **Broker deploy `401 Unauthorized`** from an older run: Terraform no longer deploys broker code
  (basic-auth publishing stays disabled); "Setup Data Provider"/"Setup Data Consumer" run `deploy-brokers.sh` with Entra auth.
- **Region preflight fails**: the region lacks ACL, Flex Consumption or virtual nodes v2
  confidential pods; it lists the regions that offer all three. Pick one and update the variables.

## Assumptions and limits

- `depa dc setup` is idempotent and approves the CCR releases found in `releases_dir`;
  "Run Scenario" runs `depa --json scenario deploy --stage <stage>` once per stage, each on a fresh
  runner with its own `--work-dir` (a stage falls back to the services when no earlier state is
  present), and uploads each stage's summary plus any `*receipt*` files (the work dir itself is
  never uploaded because it holds plaintext data). CLI owned by the SDK.
- `depa-env.yaml` follows `sdk/python/src/depa/env/schema.json`: `service_cert` is a path
  relative to the env file (`certs/kms.pem`, `certs/cs.pem`); each demo TDP keeps all its
  datasets in one `datasets` container, so `storage.<party>.uri` is that container and the
  SDK's `<uri><dataset name>/<version>/` lands there. Its broker grants `dataset:<party>/*`
  (any dataset of that owner, ciphertext only: keys are still released per dataset by the KMS).
- Demo deployment only: the deployment principal gets *Storage Blob Data Contributor* on TDP data accounts
  and TDC output accounts (`operator_object_ids`) because the workflows act for every party
  (upload datasets, download models, delete a scenario's outputs on Tear down Scenario); clean
  rooms themselves only ever use broker-issued SAS.
- Demo deployment only: the KMS and CS Key Vaults use firewall default action `Allow`
  (`key_vault_network_default_action`), because GitHub-hosted runners have changing egress IPs
  and Terraform reads the vault certificates while refreshing state. Data-plane access still
  requires Entra RBAC. Production keeps `Deny` and runs Terraform from an allowed IP range or a
  self-hosted runner in an allowed subnet.
- Demo deployment only: the Key Vaults have no purge protection and a 7-day soft-delete retention
  (`key_vault_purge_protection_enabled = false`, `key_vault_soft_delete_retention_days = 7` in the
  `kms` and `contract-service` roots), and the azurerm provider purges them on destroy
  (`purge_soft_delete_on_destroy`), so Tear down KMS / Tear down Contract Service free the vault
  names and the next deployment can reuse them. Everything else (vaults, member certificates,
  firewall handling) is as in production, which keeps purge protection on (the service repos'
  default). Purge protection cannot be turned off on an existing vault: this applies to fresh
  deployments.
- Demo deployment only: the clean-room cluster runs a single system node on the Free AKS tier
  (`ccr-provider-cluster`), and can be paused between runs (**Pause CCR Provider** /
  **Resume CCR Provider**).
- Egress from clean rooms is allowed to `AzureCloud.<region>` on 443 because ACL has no service
  tag (defence in depth only; the CCR is the boundary).
- ACL user-defined endpoints (custom JS apps) must be available in the chosen region.
