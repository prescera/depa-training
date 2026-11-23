# Quick start (local, no cloud)

This runs the real KMS and Contract Service in local CCF sandboxes, then a complete scenario:
providers encrypt and publish, the contract is proposed and signed, a clean room runs and
receipts are verified. The clean room uses the development-only `virtual` platform, so there is
no hardware attestation. Everything else is the production code path.

## Prerequisites

- Linux or WSL 2 with Docker, Python 3.12, Node 22 and Git, or use the dev container
  ([`.devcontainer`](../../.devcontainer)).
- The two service repositories checked out next to this one:

```
~/depa_training/            # or any directory; then set DEPA_KMS_REPO / DEPA_CS_REPO
  depa-training/            # this repository
  depa-training-kms-azure/
  contract-service/
```

```bash
mkdir -p ~/depa_training && cd ~/depa_training
for r in depa-training depa-training-kms-azure contract-service; do
  git clone https://github.com/prescera/$r.git
done
cd depa-training
```

## Install

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e 'sdk/python[dev]'
# Trainer dependencies for the scenario's family (classic | torch | llm), hash-locked, CPU-only:
pip install --require-hashes -r trainers/requirements/torch.txt && pip install --no-deps -e trainers
(cd ../depa-training-kms-azure && npm ci && make build)
(cd ../contract-service && npm ci && make build)
```

## Run

```bash
depa dev up                       # both sandboxes, governance bootstrap, virtual release approved
depa dev scenario run covid       # prepare → encrypt → publish → contract → clean room → receipts → model
depa run list --contract <contract_id>
depa dev down --purge
```

Swap `covid` for `credit-risk`, `mnist`, `cifar10`, `brats` or `llm-sft`. Each scenario's
`README.md` describes its parties, datasets and recipe.

## What just happened

1. `dev up` bootstrapped both services with a generated DC member, pinned each service's signing
   keys in the other, allowed `virtual` attestation and approved the `virtual-dev` release.
2. Each TDP generated its dataset key in the KMS and set a release policy. It then encrypted its
   prepared data into `dedf/1`, uploaded it and published a descriptor.
3. The TDC proposed the scenario contract, with the recipe embedded by digest. Every TDP ran
   `contract verify` and signed.
4. The clean room authorised a run, obtained each dataset key, verified and decrypted the data,
   and ran `depa-train` in a sandboxed subprocess. It then gated and encrypted the model and
   posted the execution receipt.
5. The TDC fetched and decrypted the model. The receipt was verified against both services and
   the CCF ledger.

Next: [Azure pilot](azure-pilot.md) · [Production deployment](production.md) ·
[Architecture](../architecture.md)
