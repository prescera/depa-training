# depa — DEPA Training v2 SDK, CLI and clean-room agent

Python ≥ 3.11. Implements `docs/specs/protocol.md`: JCS/JWS/HPKE primitives, the `dedf/1`
encrypted dataset format, typed KMS and Contract Service clients (TLS pinned to the CCF service
certificate), contract authoring and verification, storage connectors with OIDC federation,
attestation providers, the clean-room runtime agent (`depa-ccr run`), job submitters and the
`depa` CLI.

```bash
pip install -e 'sdk/python[aws]'      # boto3 only needed for S3
depa --help
```

## Concepts

* **Profile** (`$DEPA_HOME`, default `~/.depa`): `config.yaml` (service URLs, pinned certs,
  default party) and `keys/<party>/{signing.jwk,hpke.jwk,party.json}` (private keys 0600).
* **Environment file** (`--env depa-env.yaml`): generated from Terraform outputs; replaces the
  profile's services and maps each party to a key directory. Schema:
  `src/depa/env/schema.json`; check with `depa env validate`.
* Every mutating call is a signed request by the acting party (`--party <id>`).

### Environment file

```yaml
collaborative:
  kms: {url: https://<kms>/app, service_cert: certs/kms.pem, oidc_issuer: https://<gateway>/app/oidc}
  cs:  {url: https://<cs>/app,  service_cert: "-----BEGIN CERTIFICATE-----..."}   # path or inline PEM
parties:
  - {party_id: dc-op, roles: [dc-member], key_dir: keys/dc-op}
  - {party_id: icmr, roles: [tdp], key_dir: keys/icmr}
  - {party_id: tdc, roles: [tdc], key_dir: keys/tdc}
storage:          # per TDP: base location; datasets go to <uri><name>/<version>/
  icmr: {connector: azure-blob, uri: "https://acct.blob.core.windows.net/icmr/",
         federation: {azure: {broker_url: "https://<broker>/api", audience: "api://depa-broker-icmr"}}}
outputs:          # per TDC
  tdc: {connector: azure-blob, uri: "https://acct.blob.core.windows.net/outputs/",
        federation: {azure: {broker_url: "https://<broker>/api", audience: "api://depa-broker-tdc"}}}
compute:
  aks: {subscription_id: ..., resource_group: ..., cluster_name: ..., namespace: depa, context: <kubectl ctx>}
  gcp: {project: ..., zone: ..., network: ..., subnetwork: ..., service_account: ..., machine_type: c3-standard-4}
registry: {login_server: <acr>.azurecr.io}
releases_dir: ../../ccr/releases      # ccr/releases/<release_id>.json manifests
```

Locations and federation blocks are the descriptor shapes of spec §7.2 / §10 (`aws.role_arn`,
`gcp.audience`, `azure.{broker_url,audience}` per §10.1, or `azure.{tenant_id,client_id}` once
RS256 is available). `key_dir` directories have the profile layout; generate them with
`depa --env f.yaml party keygen --party-id <id> --name ... --roles ...`. Relative paths resolve
against the environment file.

## Command tree

Global options: `--json` (machine-readable output), `--home DIR`, `--env FILE`, `--party ID`,
`--kms-url/--kms-cert/--cs-url/--cs-cert`. Exit codes: 0 ok, 1 error, 2 usage/config,
3 validation or verification failure, 4 service/storage/deploy error, 5 not found.

| Command | Purpose |
|---|---|
| `party keygen --party-id --name --roles [--signing-kid k1 --hpke-kid h1]` · `party show` | Keys and the public party record |
| `env validate FILE` | Schema + consistency check of an environment file |
| `dc setup --env FILE [--client-cert --client-key \| --operator-token-env VAR] [--allow-virtual-attestation]` | Idempotent governance (safe on every run): bootstrap if needed (operator mTLS, a bearer from `VAR`/`$DEPA_OPERATOR_TOKEN`, or `az account get-access-token` for ACL), register parties, pin peers, approve every manifest in `releases_dir` in **both** services (identical ones skipped), set `trust_bundle_digest` and OIDC settings |
| `dc trust-bundle` | Print the CCR trust bundle, its digest and the digest each service expects |
| `dc bootstrap --member p.json... [--service kms\|cs\|both]` | Operator bootstrap (once) |
| `dc propose ACTIONS.yaml` · `dc vote ID approve\|reject --service kms\|cs` · `dc proposals` · `dc members` | Governance |
| `dc settings --allow-virtual-attestation BOOL` · `dc pin-peers` · `dc audit` | Settings, peer pinning, deployment audit |
| `dc release approve ccr/releases/<id>.json` · `dc release revoke ID` · `dc release list` | CCR release registry (identical proposals to KMS and CS) |
| `dataset create-key T` · `dataset policy set T [--policy F]` · `dataset policy show ID` | KMS key and release policy (owner) |
| `dataset encrypt T --out DIR` · `dataset upload T --encrypted DIR` · `dataset publish T --encrypted DIR` | `dedf/1` encryption, upload (manifest last), catalog entry |
| `dataset rotate ID [--retire-previous]` · `dataset suspend ID` · `dataset resume ID` | Key rotation and kill switch |
| `contract init TEMPLATE --out C.json` · `contract validate C.json` | Render (`contract_id`, validity, recipes, manifest digests) and validate |
| `contract verify C.json\|ID` · `contract propose C.json` · `contract sign ID` · `contract status ID` · `contract revoke ID --reason` | Signature flow; `sign` refuses unless `verify` passes |
| `job submit --platform aks\|gcp\|local --contract-id ID [--release M.json] ...` · `job status NAME` · `job logs NAME` | Deploy a clean room |
| `run list --contract ID` · `run receipt RUN` · `run verify-receipt RUN [--txid T]` | Execution Receipts (+ CCF ledger proof) |
| `model fetch --run RUN --out DIR` · `model decrypt DIR --run RUN --out DIR` | Outputs (manifest checked against the verified receipt) |
| `scenario deploy NAME [--env FILE] --platform aks\|gcp\|local [--parties all\|a,b]` | End-to-end driver (below); without `--env`, `--platform local` uses the `depa dev up` profile |
| `dev up` · `dev down [--purge]` · `dev scenario run NAME` | Local CCF sandboxes, virtual attestation |

`T` is a dataset template (`scenarios/<name>/datasets/<x>.yaml`): a catalog descriptor plus
SDK-local `source` (plaintext dir relative to the scenario, `include` globs) and `policy`.
Templates use `${VAR}` / `${VAR:-default}` substitution.

## `depa scenario deploy`

For pilots and demos where one operator holds several parties' keys. Idempotent; every step is
the same code as the individual commands, so in production each party runs only its own steps:

1. per TDP whose keys are held: `dataset create-key` (if absent), `policy set` (if changed;
   the policy allows the chosen platform, the TDC, and the TDP's storage audience),
   `prepare/prepare_all.sh` when source data is missing (`--prepare auto|always|never`),
   `encrypt`, `upload`, `publish` — skipped when the version is already in the catalog;
2. TDC: render the contract for the platform and the releases in `releases_dir`, output location
   from `outputs.<tdc>`; reuse an identical usable contract, else `propose`;
3. each required signer whose keys are held: `contract verify` then `sign`;
4. `job submit` (aks: `compute.aks`; gcp: `compute.gcp`; local: in-process, virtual attestation);
   wait for the run in the CS (`--wait-timeout`);
5. `run verify-receipt`, `model fetch` + `decrypt` with the TDC key.

Each run writes `receipt-<run_id>.json` (CS-signed receipt JWS and its verified claims, public
evidence only) into `--work-dir`; no other file there matches `*receipt*`.
Python API: `depa.deployment.deploy_scenario(scenario, env, profile, DeployOptions(..., cancel=threading.Event()))`
(setting the event raises `DeployCancelled` promptly and terminates local CCR / prepare
subprocesses; the CCR kills its trainer and reports `failed`), `locate_dataset_template(env, template)`,
`environment_from_profile(profile, scenarios=[...])` (development), and
`workflows.encrypt_dataset(..., progress=lambda path, done, total: ...)`. The `virtual` release's
provenance source defaults to `https://github.com/prescera/depa-training` (`DEPA_SOURCE_REPO`);
scenario `terms.uri` defaults use `DEPA_TERMS_BASE_URL`.
Prints a summary JSON (`status`: `succeeded`, `awaiting-signatures`, `contract-active`,
`datasets-published`; `steps`, `skipped`, `result.{run_id,metrics,privacy_spent,outputs}`).
Bearer tokens for party-side Azure/GCS storage are read from environment variables
(`--storage-token-env`, `--output-token-env`), never from argv.

## Clean-room agent

`depa-ccr run` (container command `["depa-ccr","run"]`, work dir `/depa`). Inputs:
`DEPA_CONTRACT_ID`, `DEPA_RECIPE_DIGEST`, `DEPA_CS_URL`, `DEPA_KMS_URL`, `DEPA_PLATFORM`,
`DEPA_TRUST_BUNDLE` (required on `aci-snp`/`gcp-cs-tdx`; base64url JSON
`{v:1, cs:{url,service_cert_pem,jwks}, kms:{...}}` with `url` = the `/app` base URL, the service
certificate PEM verbatim and the `/info` JWKS; its digest is bound in the attested binding and
checked by both services), `DEPA_LOG_LEVEL`. The image sets `DEPA_CCR_TRAINER`
(an executable under `/opt/depa/`). Development only (`virtual`): `DEPA_WORK_DIR`,
`DEPA_TRAINER_CMD`. The RAT is refreshed (`POST /contracts/{id}/runs/{run}/rat`, run-key signed)
when near expiry; output keys and storage credentials are obtained only at upload time. Tier-3
code fails closed without network isolation. On `gcp-cs-tdx` the submitter mounts a tmpfs at
`/depa` (`tee-mount`) and creates the VM without an external IP (`--no-address`).
Status lines on stderr are allowlisted JSON events; error detail appears only on `virtual` at
`debug`.

## Development

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e 'sdk/python[dev]'
cd sdk/python && pytest -q && ruff check src tests && mypy
pytest ../../tests/e2e -q        # HTTPS mock DC always; real KMS/CS sandboxes when available
```
