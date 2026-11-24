# Identities, contracts and keys

How parties are identified, how a contract is signed, and how dataset keys and release policies
work. The wire formats are in the [protocol specification](../specs/protocol.md) (§3 identity, §4
governance, §7 datasets, §8 contracts, §9 runs).

## Identities

Every party (provider, consumer, clean-room provider, DC member, auditor) is an organisation
with its **own keys**, generated and held by that organisation:

| Key | Algorithm | Used for |
|---|---|---|
| Signing key | ECDSA P-256 (ES256) | Every request it makes: dataset keys and policies, catalogue entries, contract proposals, signatures, revocations, governance votes |
| HPKE key | X25519 | Receiving encrypted outputs (trained models) |

```bash
depa party keygen --party-id bank-a --name "Bank A Ltd" --roles tdp
depa party show --party bank-a          # public record to give to the collaborative
```

Private keys never leave the party. Today they are files with mode 0600 under `$DEPA_HOME`. The
SDK signs through a `Signer` interface, so an HSM or Azure Key Vault signer can replace the
files without other changes.

**Registration is a governance decision.** A party sends its public record (ids, roles, public
keys) to the Data Collaborative. The members vote to register it in **both** the KMS and the
Contract Service (`set_party`, majority by default). From then on, the services accept only
requests signed by that party's registered key. Each signed request carries an audience, an
operation name, a timestamp and a nonce, and names its target, so it cannot be replayed or
redirected.

This replaces v1's approach, which combined all providers into one entity and identified
parties by GitHub `did:web` documents. In v2:

- each provider is a separate party;
- identity is anchored in the collaborative's registry, on a ledger, under multi-party
  governance;
- no external identity provider is needed. An organisation's DID or X.509 certificate can be
  recorded in its party record if the collaborative's charter requires it.

## Contracts

A contract binds a consumer, specific dataset versions, the exact recipe, allowed clean-room
releases and platforms, privacy parameters, output recipients, a time window and a run budget
(schema: spec §8.1).

| Step | Who | Command | What is checked |
|---|---|---|---|
| Draft | Consumer | `depa contract init scenarios/<name>/contract.yaml --out contract.json` | Fills in `contract_id`, validity, recipe digests and the catalogue's manifest digests |
| Propose | Consumer | `depa --party tdc contract propose contract.json` | The CS validates parties and roles, datasets and digests, releases and recipes |
| Review | Each provider | `depa --party bank-a contract verify <contract_id>` | Datasets and versions, manifest digests, recipe, releases, platforms, privacy against the provider's own policy, output recipients, legal text digest |
| Sign | Each required party | `depa --party bank-a contract sign <contract_id>` | Signs the contract digest. It refuses unless `verify` passes. |
| Active | — | `depa contract status <contract_id>` | Becomes active when the consumer and every dataset owner have signed |
| Revoke | Any signatory | `depa --party bank-a contract revoke <contract_id> --reason …` | Takes effect immediately for new runs and at a running job's next token refresh |

Contracts cannot be edited. A change means a new contract that every affected party signs
again. The same flow is available in the web console (`depa-console`): the Contracts page shows
the contract in plain language and enables **Sign** only when verification passes.

## Dataset keys and release policies

v1 had each provider generate a key, import it into Azure Key Vault and attach a secure key
release policy bound to the clean room's CCE policy hash. v2 replaces that with the
collaborative's KMS:

1. **Create the dataset key in the KMS.** The KMS generates an HPKE key pair for the dataset
   inside CCF. Only the public key ever leaves it unwrapped.
   ```bash
   depa --party bank-a dataset create-key scenarios/credit-risk/datasets/bank-a-credit-applications.yaml
   ```
2. **Set the release policy.** Only the owner can do this, and no vote can relax it. It lists
   the allowed platforms, tiers, consumers, purposes, the DP floor, output modes, storage token
   audiences and an expiry. Every version is kept.
   ```bash
   depa --party bank-a dataset policy set <template> [--policy policy.json]
   depa dataset policy show bank-a/credit-applications
   ```
3. **Encrypt locally and upload.** A fresh data key encrypts the files (authenticated streaming
   encryption) and is sealed to the dataset's public key. The manifest is signed by the owner.
   ```bash
   depa --party bank-a dataset encrypt <template> --out ./enc
   depa --party bank-a dataset upload  <template> --encrypted ./enc
   depa --party bank-a dataset publish <template> --encrypted ./enc
   ```
4. **Key release, enforced by the KMS.** A clean room obtains the private key only through
   `POST /keys/release`, which checks everything in one ledger transaction:
   - the hardware attestation and its run binding;
   - the Contract Service's run authorisation token;
   - an approved clean-room release named in the contract;
   - the owner's policy;
   - suspensions.

   The key is wrapped to a key that exists only inside that clean room, and the release is
   recorded with a signed receipt. Storage tokens (`/oidc/token`) go through the same checks.
5. **Owner controls at any time:** `depa dataset rotate` (new key version),
   `depa dataset suspend` / `resume` (kill switch, immediate and independent of the contract).

**Per-organisation Key Vault (hold your own key)** is a planned option, not yet implemented.
In the intended design the data key is split between the central KMS and the provider's own
Key Vault, so both must agree to release it.
