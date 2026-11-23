# Data Collaboratives — Market Architecture

Technology alone does not create a data economy; institutions do. **Data Collaboratives** are
the market architecture through which DEPA Training is used: self-regulated consortiums that
make multilateral training collaborations cheap, safe and repeatable.

## 1. The problem with bilateral deals

Today every data collaboration is a bespoke bilateral engagement. Each provider–consumer pair
negotiates terms, runs due diligence, builds an integration and then monitors compliance by
audit. The transaction costs (finding partners, negotiating, enforcing) dominate the value of
most deals. So most never happen, and the most sensitive and most valuable data is never used.

With *m* providers and *n* consumers, bilateral collaboration needs up to **m × n** negotiated
contracts and integrations, each enforced only after the fact.

## 2. What a Data Collaborative is

A Data Collaborative (DC) is a consortium, usually sector-specific (health, credit, agriculture,
mobility…). Its members agree on a charter and jointly operate the DEPA trust services:

| Member type | Joins to |
|---|---|
| Training Data Providers (data fiduciaries) | Offer datasets under their own release policies, and earn from their use |
| Training Data Consumers | Discover datasets and train on them under standard terms |
| Clean-room providers | Offer attested compute as a service |
| Auditors / regulators | Observe ledgers and receipts; may hold governance seats |
| Operator | A neutral body (industry association, SRO, consortium entity) that runs the infrastructure on the members' behalf |

The DC runs the **Contract Service** (registry, catalogue, contracts, receipts) and the **KMS**
(keys, owner policies, storage federation). Both are transparent CCF ledgers governed by the
members' votes, not by the operator.

## 3. Why it changes the economics

| Cost | Bilateral today | With a Data Collaborative |
|---|---|---|
| Onboarding and legal | m × n contracts | **m + n**: each party signs the DC charter once; engagements then use standard contract templates |
| Discovery | Personal networks, brokers | Shared catalogue of dataset descriptors (schema, size, terms), with no data exposed |
| Integration | Custom pipelines per pair | One encryption format, one SDK, any cloud storage |
| Trust and due diligence | Repeated per partner | Shared: attested clean rooms, approved releases, governance-approved code |
| Monitoring and enforcement | Audits after the fact, litigation | Technical enforcement before the fact (keys released only under contract), plus ledger receipts |
| Infrastructure | Each organisation runs its own vaults and clean rooms | Shared KMS and Contract Service. Per-organisation key vaults stay optional. |

Lower transaction costs make many more engagements viable. That includes multilateral ones, in
which a model learns from many providers at once: for example, credit risk across banks,
bureaus and fintechs, or diagnostics across hospitals.

## 4. Rights that stay with the data owner

Joining a collaborative never means handing control to it.

- **Purpose limitation:** each contract binds data to a purpose, exact recipes and approved clean-room code.
- **Granularity:** consent is per dataset version (by manifest digest), per consumer, per platform, per tier, with privacy floors and output modes chosen by the owner.
- **Revocability:** owners can revoke a contract or suspend a dataset at any time, immediately, without anyone else's permission.
- **Auditability:** every policy change, signature, key release and run is on a tamper-evident ledger with verifiable receipts.
- **Tighten-only governance:** the collaborative can suspend a dataset or revoke a release. It can never relax an owner's policy, touch an owner's keys or change a signed contract.

## 5. Self-governance

The charter maps directly onto the services' app-level governance:

| Charter element | Enforced by |
|---|---|
| Membership admission and removal | `set_member` / `set_party` proposals, unanimous or super-majority |
| Approved clean-room releases | `approve_ccr_release` after reproducible-build review |
| Voting thresholds per decision type | `set_constitution` |
| Incident response | `suspend_dataset`, `revoke_ccr_release`, `revoke_contract` |
| Transparency | Ledger receipts; proposals and ballots stored with signatures |
| Code integrity of the services themselves | `approve_app_digest`, plus continuous audit of deployed bundles |

## 6. Engagement lifecycle

1. **Join:** sign the charter; governance registers the party and its keys.
2. **Offer:** a TDP encrypts a dataset into its own storage, sets a release policy and publishes a descriptor to the catalogue.
3. **Discover and propose:** a TDC finds datasets and proposes a contract from a DC template. The contract embeds recipes, privacy, outputs, time window and legal terms (by digest).
4. **Consent:** each data owner verifies and signs. The contract becomes active.
5. **Train:** clean rooms run within the contract's limits. Each run produces an execution receipt.
6. **Settle and audit:** receipts drive billing and revenue sharing according to the contract's commercial terms, and auditors verify compliance from the ledger.

## 7. Value for data principals

Data fiduciaries can monetise data they hold without exposing it, and the contract's terms can
pass value to the data principals it describes: benefit-sharing, sector funds or improved
services. Because the data never leaves the provider's control and every use is purpose-bound
and auditable, sensitive data that is unusable today becomes a source of shared value. That is
the aim of DEPA as digital public infrastructure.
