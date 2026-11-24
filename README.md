# DEPA Training

**Train AI models on many organisations' data without any of them giving their data away.**

DEPA Training is a techno-legal framework and open digital public infrastructure for
privacy-preserving model training on multi-party data. The model comes to the data:
training runs inside hardware-attested *confidential clean rooms* (CCRs), and each data
provider's data can be decrypted only inside a clean room that runs approved code, under a
contract that provider has signed.

> This is DEPA Training v2, a production-grade re-architecture of the v1 proof of concept
> (single resource group, ACI deployment scripts), which is preserved in the git history.

## Why a new paradigm

Today, collaborating on data means **surrendering control**: data is copied to the consumer or to
a third party, and from then on the provider must trust contracts and audits rather than
technology. So the most valuable data, the sensitive data, stays locked away.

| | Today | DEPA Training |
|---|---|---|
| Where data goes | Copied to the model owner or an intermediary | Stays in the provider's own storage. It is decrypted only inside an attested clean room. |
| Who controls use | Whoever holds the copy | The provider. Keys are released per dataset, under the provider's own policy, to approved code only. |
| Contracting | Bespoke bilateral deals: *m × n* contracts | Sign once with a Data Collaborative: *m + n* onboarding, then many-to-many engagements |
| Enforcement | Legal recourse after the fact | Technical, before the fact (attestation, key release, purpose binding), plus legal |
| Evidence | Self-reported | Signed, ledger-backed receipts for every training run |

The result is that sensitive data can be **monetised without being exposed**. Data principals
and fiduciaries can unlock economic value from data they hold, and data consumers gain access
to training data that was previously out of reach.

## Core principles

1. **The model comes to the data.** Providers keep their storage. Plaintext exists only in TEE memory.
2. **Control stays with the provider.** Every dataset has its own key and a release policy written by its owner. No consortium vote can relax it.
3. **Purpose limitation, granularity, revocability, auditability.** Contracts bind a dataset to a
   purpose, a recipe, a clean-room release, a time window and a number of runs. Owners can revoke or suspend at any time. Every run leaves verifiable receipts.
4. **Verify, don't trust.** Operators of compute, storage and even the trust services are not
   trusted with plaintext. Decisions depend on attestation and transparent, multi-party-governed ledgers.
5. **Structured, checkable computation.** Training is expressed so it can be verified automatically:
   declarative recipes first, graph-only models next, approved custom code last.
6. **Open and multi-cloud.** Data can live on any cloud. Clean rooms run on Azure and GCP confidential computing. The trust services are open source.

## Actors

| Actor | Role |
|---|---|
| **Training Data Provider (TDP)** | Owns datasets. Encrypts them, sets key-release policies, signs contracts, can revoke. |
| **Training Data Consumer (TDC)** | Wants a model. Proposes contracts and recipes, receives outputs according to the contract. |
| **Data Collaborative (DC)** | A self-regulated consortium. Operates the Contract Service and KMS, admits members, approves clean-room releases. |
| **Clean-room provider (CCRP)** | Optional vendor that runs clean rooms for a TDC. Never sees plaintext. |
| **Auditor / regulator** | Reads the ledgers and receipts. May be a DC member. |

## Architecture at a glance

```
 GOVERNANCE PLANE — operated by the Data Collaborative (CCF apps on Azure Confidential Ledger)
   Contract Service: parties, dataset catalogue, contracts, run authorisation, execution receipts
   KMS:              per-dataset keys + owner policies, single atomic key-release check, OIDC issuer
        ▲ sign / set policy          ▲ attest → authorise run → release keys / storage tokens
 DATA PLANE (each TDP)          COMPUTE PLANE (TDC or CCRP — untrusted operator)
   own storage on any cloud       time-bound clean rooms: AKS + virtual nodes → confidential ACI (SEV-SNP)
   client-side encrypted          or GCP Confidential Space (Intel TDX)
   IAM trusts only KMS tokens     one run of one contract, then gone
```

See [docs/architecture.md](docs/architecture.md) for the full design,
[docs/threat-model.md](docs/threat-model.md) for what is protected against whom, and
[docs/data-collaboratives.md](docs/data-collaboratives.md) for the market architecture, and
[docs/guides/identities-contracts-keys.md](docs/guides/identities-contracts-keys.md) for how parties,
contract signing and dataset keys work in practice.

## Repositories

| Repository | Contents |
|---|---|
| [`depa-training`](.) (this repo) | Clean-room runtime, training runtime (`trainers/`), SDK and `depa` CLI (`sdk/`), Terraform for providers and consumers (`deploy/`), clean-room images and releases (`ccr/`), scenarios, docs |
| [`contract-service`](https://github.com/prescera/contract-service) | Contract Service CCF app and its Azure Terraform |
| [`depa-training-kms-azure`](https://github.com/prescera/depa-training-kms-azure) | KMS CCF app (based on the DEPA-inferencing KMS) and its Azure Terraform |

## Layout of this repository

```
docs/            architecture, threat model, data collaboratives, protocol spec, guides
sdk/python/      `depa` SDK + CLI + clean-room agent (`depa-ccr`)
trainers/        `depa-train`: tiered, declarative training runtime and plugins
ccr/             clean-room image build, CCE policy inputs, release manifests
deploy/          Terraform: TDP storage + federation, TDC compute (AKS/VN2, GCP CS), DC composition
scenarios/       end-to-end examples: recipe, dataset descriptors, contract template, prep scripts
tests/e2e/       local end-to-end tests (CCF sandboxes + virtual clean room)
gui/             minimal web console over the SDK
```

## Quick start

- **Local, no cloud:** [docs/guides/quickstart.md](docs/guides/quickstart.md). Runs both trust
  services in CCF sandboxes, then a full scenario (encrypt → contract → clean-room run →
  receipts → model) with a development-only virtual clean room.
- **Azure, one click:** after a one-time setup, the **End-to-end demo** workflow deploys a
  complete collaborative and runs a scenario. See [docs/guides/azure-single-subscription.md](docs/guides/azure-single-subscription.md).
- **Console:** `depa-console` is a minimal local web UI over the SDK, acting as one party at a
  time ([gui/README.md](gui/README.md)).

## Scenarios

| Scenario | Task | Tier / family | Privacy | TDPs |
|---|---|---|---|---|
| [COVID-19](scenarios/covid) | Binary classification, horizontal join of tabular data | 1 / torch | DP-SGD | 3 |
| [BraTS](scenarios/brats) | MRI tumour segmentation (UNet) | 1 / torch | DP-SGD | 4 |
| [Credit risk](scenarios/credit-risk) | XGBoost, joins across banks, bureau and fintech | 1 / classic | DP | 4 |
| [CIFAR-10](scenarios/cifar10) | Image classification (CNN) | 1 / torch | none | 1 |
| [MNIST](scenarios/mnist) | Image classification (CNN) | 1 / torch | none | 1 |
| [LLM SFT](scenarios/llm-sft) | LoRA fine-tuning of a small language model (CPU) | 1 / llm | optional | 2 |

## Status

v2 is under active development for production pilots. Read the threat model's residual risks
before relying on it. Differential privacy is supported but treated as an additional safeguard,
not the primary control.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
