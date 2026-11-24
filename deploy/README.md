# Deploying DEPA Training v2

| Path | What |
|---|---|
| [`terraform/`](terraform) | Modules and per-role environments (multi-subscription, multi-cloud production layout). |
| [`terraform/environments/azure-single-subscription`](terraform/environments/azure-single-subscription) | One Azure subscription, one resource group per party: DC services (KMS + CS on ACL), each TDP's storage + SAS broker, TDC output storage, TDC AKS + virtual nodes v2 cluster, ACR. |
| [`scripts/`](scripts) | `demo-parties.py` (the fixed demo parties), `deploy-brokers.sh` (SAS broker code → function apps, Entra auth), `deploy-services.sh` (KMS/CS bundles → ACL), `fetch-kms-jwks.sh` (KMS JWKS to pin in brokers), `build-releases.sh` (CCR images → ACR, CCE policies, release manifests), `render-env.sh` (Terraform outputs → `depa-env.yaml`), `party-keys.sh` (party keys ↔ GitHub secret). |
| `../.github/workflows/` | **Set up collaborative** (`setup-collaborative.yml`), **Set up collaborators** (`setup-collaborators.yml`), **Run scenario** (`run-scenario.yml`), **Tear down** (`teardown.yml`), each `workflow_dispatch` + `workflow_call`, Azure login with GitHub OIDC, gated by a GitHub environment; **End-to-end demo** (`end-to-end.yml`) chains them; **End-to-end demo (PR)** runs it for PRs labelled `run-azure-demo`; **Local end-to-end** is the always-on sandbox check. |

## Single-subscription Azure deployment: one-time setup

Everything below is run once by a subscription Owner with `az` and `gh` (GitHub CLI) logged in.
No client secret is ever created: GitHub Actions authenticates with a federated credential.

```bash
SUB=<subscription-id>
REPO=prescera/depa-training          # this repository; the service repos are checked out from the same owner
ENV=<environment>                     # GitHub environment name (also the state key prefix), e.g. demo
LOC=centralindia                      # offers ACL, VN2 confidential pods and Flex Consumption (checked by "Set up collaborative")
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

### Optional: GCP clean rooms (`platform = gcp` in "Run scenario")

Deploy [`environments/tdc-gcp-cs`](terraform/environments/tdc-gcp-cs) and set `GCP_PROJECT`,
`GCP_ZONE`, `GCP_SERVICE_ACCOUNT` (workload SA), `GCP_SUBNET`, `GCP_WORKLOAD_IDENTITY_PROVIDER`
and `GCP_SUBMITTER_SERVICE_ACCOUNT` (a job-submitter member, impersonated via Workload
Identity Federation from this repository) as environment variables.

## Single-subscription Azure deployment: deploy and run (Actions tab, choose the environment each time)

**One click:** **End-to-end demo** (inputs: environment, scenario, platform, teardown) runs the
three workflows below in order; teardown runs even after a failure when selected. Label a pull
request `run-azure-demo` to run it for credit-risk with teardown in the environment named by the
repository variable `DEMO_ENVIRONMENT` (same-repository PRs only). The workflows mirror the
DEPA Training flow:

| Workflow | Jobs / steps | Does |
|---|---|---|
| **Set up collaborative** | *Trusted services infrastructure* | region check (ACL, Flex Consumption, VN2 confidential pods); Terraform `1-platform`: party resource groups, KMS + contract-service ledgers (Key Vaults, member certs), container registry, Log Analytics, the consumer's AKS cluster |
| | *KMS and contract service* | builds both bundles, `PUT /app/userDefinedEndpoints` on each ledger, `depa dc setup` (bootstrap → KMS OIDC key, members, parties, settings) |
| | *Clean-room releases* (classic, torch, llm) | images → registry, CCE policies, release manifests (artifacts `ccr-releases-<env>-<family>`; version `2.0.0-demo.<run id>` because release ids are immutable once approved) |
| **Set up collaborators** | *Provider and consumer resources* | Terraform `2-workloads`: per provider (`tdc`, `tdp-1`…`tdp-4`) storage + SAS broker (KMS JWKS pinned), consumer output storage + broker, virtual nodes release, namespace, RBAC; removes a stray `vn2` namespace; deploys the broker code (`deploy-brokers.sh`, Entra auth) |
| **Run scenario** | Register parties and releases → Dataset keys and policies → Encrypt and upload data → Sign contract → Train in clean room → Download model | `depa dc setup` (idempotent), then one `depa scenario deploy --stage keys/data/contract/train/model` step each; uploads stage summaries + receipts |
| **Tear down** | Destroy collaborator resources → Destroy trusted services infrastructure | `terraform destroy` of `2-workloads`, then `1-platform` |

Both setup workflows take `apply` (default on; off = Terraform plan only). Any scenario runs on
the same deployment: pick it in **Run scenario** (or in **End-to-end demo**).

The consumer's AKS cluster is still created by *Trusted services infrastructure* (Terraform phase
`1-platform`): the Kubernetes and Helm providers of phase `2-workloads` need an existing cluster.
Moving it into collaborator setup would need a fresh deployment (the Terraform phases and state
keys `depa-training/<environment>/<phase>.tfstate` are unchanged so existing deployments keep
working).

### Why no public KMS gateway is needed

The KMS signs ES256 OIDC tokens. Azure Blob access goes through each TDP's
[federation broker](terraform/modules/azure/federation-broker) (spec §10.1), which pins the KMS
JWKS (fetched over TLS pinned to the KMS ledger identity and cross-checked with `GET /info`), so
no publicly trusted issuer endpoint is involved and the issuer is the ledger URL
(`https://<kms-ledger>.confidential-ledger.azure.com/app/oidc`). Datasets on AWS/GCP need
the public issuer: add [`environments/dc/2-gateway`](terraform/environments/dc) using the
`kms_gateway_stack_inputs` output of `1-platform`.

### Running locally instead of from Actions

```bash
cd deploy/terraform/environments/azure-single-subscription/1-platform
cp terraform.tfvars.example terraform.tfvars            # parties: ../../../../scripts/demo-parties.py > parties.auto.tfvars.json
terraform init && terraform apply                       # DC stacks from github.com/prescera/*@main (or local_override.tf.tpl for checkouts)
terraform output -json platform > /tmp/platform.json
../../../../scripts/deploy-services.sh --platform-json /tmp/platform.json --kms-repo <kms checkout> --cs-repo <cs checkout>
# depa dc setup …, then:
cd ../2-workloads
jq '{platform: .}' /tmp/platform.json > platform.auto.tfvars.json
# set kms_jwks_json (output of deploy/scripts/fetch-kms-jwks.sh <kms-ledger>) in terraform.tfvars
terraform init && terraform apply
# Broker code (Terraform creates the apps; basic-auth publishing stays disabled):
terraform output -json workloads | jq .brokers > /tmp/brokers.json
pkg=$(../../../modules/azure/federation-broker/build-package.sh)
../../../../scripts/deploy-brokers.sh --package "$pkg" --from-json /tmp/brokers.json
deploy/scripts/render-env.sh --parties-json parties.json --keys-dir ./demo-keys --releases-dir ccr/releases --out depa-env.yaml
```

## Troubleshooting

- **`AADSTS700213: No matching federated identity record found`**: the federated credential's
  subject does not match the token. Compare it with the subject in the error message (or
  `gh api repos/$REPO/actions/oidc/customization/sub`) and add a credential with that exact
  subject plus `:environment:$ENV`.

- **`DEPA_PARTY_KEYS is empty`** in "Set up collaborative" or "Run scenario": the secret was set from
  an empty value (e.g. an earlier `gh secret set --body "$(…)"` whose command failed). Re-publish
  it with `party-keys.sh init` (only if you have no keys yet) and `party-keys.sh publish` as
  above. **If your environment's secret was set before `publish` existed, it may be empty: re-publish it.**
- **`<NAME> is empty: set the environment secret/variable`**: an Azure identifier secret or a
  region/naming variable is missing from the GitHub environment (step 6).
- **Helm: `namespace "vn2" exists and cannot be imported into the current release`** (after an
  earlier failed attempt that created `vn2` outside the chart): "Set up collaborators" removes the stray
  namespace before applying, as long as the virtualnode release is not installed. When applying
  by hand, run `az aks command invoke -g <tdc-rg> -n <cluster> --command "kubectl delete namespace vn2"`.
- **Broker deploy `401 Unauthorized`** from an older run: Terraform no longer deploys broker code
  (basic-auth publishing stays disabled); "Set up collaborators" runs `deploy-brokers.sh` with Entra auth.
- **Region preflight fails**: the region lacks ACL, Flex Consumption or virtual nodes v2
  confidential pods; it lists the regions that offer all three. Pick one and update the variables.

## Assumptions and limits

- `depa dc setup` is idempotent and approves the CCR releases found in `releases_dir`;
  "Run scenario" runs `depa --json scenario deploy --stage <stage>` once per stage with a shared
  `--work-dir` and uploads each stage's summary plus any `*receipt*` files (the work dir itself is
  never uploaded because it holds plaintext data). CLI owned by the SDK.
- `depa-env.yaml` follows `sdk/python/src/depa/env/schema.json`: `service_cert` is a path
  relative to the env file (`certs/kms.pem`, `certs/cs.pem`); each demo TDP keeps all its
  datasets in one `datasets` container, so `storage.<party>.uri` is that container and the
  SDK's `<uri><dataset name>/<version>/` lands there. Its broker grants `dataset:<party>/*`
  (any dataset of that owner, ciphertext only: keys are still released per dataset by the KMS).
- Demo deployment only: the deployment principal gets *Storage Blob Data Contributor* on TDP data accounts
  and *Storage Blob Data Reader* on TDC output accounts (`operator_object_ids`) because the
  workflows act for every party; clean rooms themselves only ever use broker-issued SAS.
- Demo deployment only: the KMS and CS Key Vaults use firewall default action `Allow`
  (`key_vault_network_default_action`), because GitHub-hosted runners have changing egress IPs
  and Terraform reads the vault certificates while refreshing state. Data-plane access still
  requires Entra RBAC. Production keeps `Deny` and runs Terraform from an allowed IP range or a
  self-hosted runner in an allowed subnet.
- Egress from clean rooms is allowed to `AzureCloud.<region>` on 443 because ACL has no service
  tag (defence in depth only; the CCR is the boundary).
- ACL user-defined endpoints (custom JS apps) must be available in the chosen region.
