# DEPA Training v2 — Threat Model

## 1. Security goals

| # | Goal |
|---|---|
| G1 | **Confidentiality.** A TDP's plaintext data is available only inside an attested clean room running an approved release, during an authorised run of a contract that TDP signed. |
| G2 | **Owner control.** Data is used only for the contracted purpose and recipe, within the owner's release policy, time window and run budget. The owner can revoke or suspend at any time, with effect on every future key release. |
| G3 | **Integrity.** Training consumes exactly the dataset versions named in the contract; tampering by storage or operators is detected. Outputs and receipts can't be forged. |
| G4 | **Accountability.** Every governance action, policy change, contract signature, key release and run is recorded on a tamper-evident ledger, with verifiable receipts. |
| G5 | **Output control.** Only contract-declared outputs leave the clean room, encrypted to contract-declared recipients, or kept in TEEs (`tee-only`). |

Availability is not a security goal: any operator can stop or delay work, but cannot turn that into a breach of G1–G5.

## 2. Trusted computing base

- **Hardware TEEs and their attestation roots:** AMD SEV-SNP (confidential ACI), Intel TDX (GCP Confidential Space), and the CCF nodes behind ACL.
- **Platform guest software:** Microsoft's confidential ACI utility VM (UVM endorsements are checked) and Google's Confidential Space image (attested via `swname`, `dbgstat`, `support_attributes`).
- **CCF / Azure Confidential Ledger:** the KMS and CS execute inside CCF's TEEs and their state is replicated and ledgered.
- **Approved code:** the clean-room releases, reproducibly built from public sources and approved by the DC; the KMS and CS bundles, whose digests are approved by governance; and the trainer plugins inside releases.
- **Cryptography:** HPKE (X25519/HKDF-SHA256/AES-256-GCM), AES-256-GCM streaming encryption, ECDSA P-256, RSA-OAEP-3072, SHA-256.
- **Each party's own signing keys.** Compromising a party's key lets an attacker act as that party, and no more.

## 3. Adversaries

| Adversary | Capabilities assumed |
|---|---|
| **A1 Compute operator** (TDC or CCRP) | Full control of the cluster, hosts, network, DNS, environment variables, scheduling, logs and storage of the compute plane. Can launch any image with any inputs, any number of times. |
| **A2 Storage operator** | Read, modify, delete, replay or reorder anything in a TDP's or TDC's storage. |
| **A3 Malicious consumer** | Writes recipes and contracts to maximise what it learns; may also be A1. |
| **A4 Curious co-provider** | Another TDP in the same contract that wants others' data. |
| **A5 DC members below threshold** | Propose, vote, and collude in groups smaller than the threshold. |
| **A6 DC operator / ACL Administrator** | Deploys app bundles and manages the ACL resource. |
| **A7 Network attacker** | Intercepts or modifies traffic between any components. |

## 4. Threats and mitigations

| Threat | Mitigation |
|---|---|
| A1 runs modified code to get keys | Keys are released only on hardware evidence whose measurement (CCE policy hash / image digest) belongs to a governance-approved release that is also named in the signed contract. Release ids are immutable. |
| A1 reuses a key or token from another run, or a different TEE | Binding hash in `REPORT_DATA` / `eat_nonce` ties evidence, RAT, release and OIDC token to one run, one wrapping key and one run key. RATs expire in 5 minutes. Keys are wrapped to the in-TEE key. |
| A1 points the clean room at fake services or a different contract | A fake CS cannot produce RATs the real KMS accepts (pinned keys). A different contract is still one the owners signed. The worst outcome is a failed run. |
| A1 runs a contract more often or longer than agreed | Run budget is consumed atomically at the CS. The KMS also counts distinct runs per contract against the owner's policy. Deadlines are enforced by the CS, the agent and the platform. |
| A1 reads plaintext through logs, network or disk | Production releases disable stdio; the agent emits only allowlisted status. The trainer has no credentials and no network. Plaintext lives in TEE memory only (see R6). Egress allowlists add defence in depth. |
| A2 tampers with, swaps or rolls back ciphertext | Authenticated streaming encryption (per segment, with order and truncation protection), per-object hashes, an owner-signed manifest, and a manifest digest pinned in the contract. |
| A2 harvests ciphertext for later decryption | Storage IAM admits only KMS-minted, short-lived, attested-run tokens (`sub = dataset:<id>`). |
| A3 extracts raw data via the recipe | Tier 1: schema-validated configuration through vetted plugins, a SQL allowlist, no pickle, no remote code. Tier 2: op allowlist. Tier 3: requires each owner's explicit acceptance of the measurement. The output gate restricts formats, size and metrics. DP limits per-record leakage; `tee-only` keeps the model inside TEEs. |
| A3 misrepresents the computation to owners | Recipes are embedded in the contract by digest and validated by the clean room before any key is requested. `depa contract verify` shows owners exactly what they sign. |
| A4 learns others' data | Each dataset has its own key and policy. Outputs go only to contract recipients. TDPs are not recipients unless the contract says so. |
| A5 relaxes protection | Governance cannot modify dataset policies, keys or signed contracts (tighten-only invariant). Admitting a release does not authorise it for any contract the owners did not sign. |
| A5 + A1 approve a malicious release | It must also be named in a contract that every affected owner signs. Owners verify release provenance and reproducible builds. |
| A7 tampering or eavesdropping | TLS to CCF pinned to the service identity. All authority comes from signatures (JWS, attestation, RAT, receipts), never from transport. |
| Repudiation | Signed requests stored with each state change; ledger receipts; CS-countersigned execution receipts. |

## 5. Residual risks and explicit assumptions

| # | Risk | Current treatment |
|---|---|---|
| R1 | **ACL Administrator can replace an app bundle** (ACL doesn't expose CCF member governance). | The Administrator role is held under a multi-approver process. Every bundle update is recorded on the ledger. Approved bundle digests are set by governance, and `depa dc audit` detects deviation. Being raised with the ACL team: multi-party-approved code updates. |
| R2 | **OIDC discovery and JWKS go through an Application Gateway** (public TLS), which could serve a substituted key set. | The JWKS is also served from the ledger (`/info`) for monitors to compare. Pin the JWKS directly where the cloud allows it (GCP). Treat the gateway's configuration as security-critical. |
| R2b | **Azure SAS broker (interim).** An Azure-hosted TDP's broker turns KMS tokens into SAS URLs, so it is part of that TDP's storage-access path. | It runs in the TDP's own subscription and pins the KMS JWKS. SAS URLs are scoped to the dataset prefix and expire with the token. Data stays encrypted regardless. It is retired once CCF supports RS256. |
| R3 | **Models can memorise training data.** | DP (contract-enforced, owner-floored), output gate, `tee-only` mode. DP remains an active research area; owners decide per dataset. |
| R4 | **TEE side channels and platform vulnerabilities.** | Minimum TCB/SVN in releases and UVM endorsements; revoke releases on advisories. |
| R5 | **Revocation is prospective.** In-flight runs finish (bounded by the deadline), and delivered outputs are not recalled. | Short deadlines; `tee-only` mode for high-sensitivity data. |
| R6 | **Scratch confidentiality.** Plaintext must stay in TEE-protected memory or encrypted scratch. | Platform-specific; verified per platform in the release checklist. |
| R7 | **`virtual` platform misconfiguration.** | Requires unanimous governance and explicit opt-in in each dataset policy. Production policies never list it. |
| R8 | **Party key compromise.** | Keys behind a pluggable signer (Key Vault/HSM recommended). Governance can suspend a party and rotate its keys. |

## 6. Out of scope

Denial of service by operators; correctness or fairness of the trained model; lawful basis and
consent at collection time (handled by the techno-legal framework and the contract's legal terms);
attacks on parties' endpoints outside DEPA components.
