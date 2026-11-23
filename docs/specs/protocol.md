# DEPA Training v2 — Protocol Specification

Status: draft v2.0 · Normative for: `depa-training-kms-azure` (KMS), `contract-service` (CS),
the clean-room runtime (`ccr/`), the SDK/CLI (`sdk/`) and trainers (`trainers/`).

Keywords MUST / SHOULD / MAY are used as in RFC 2119. Anything not specified here is an
implementation detail, but implementations MUST NOT weaken any check listed here.

---

## 1. Actors, services, platforms

| Term | Meaning |
|---|---|
| **DC** | Data Collaborative. Operates the KMS and CS. Its *members* govern both. |
| **TDP** | Training Data Provider. Owns datasets, their keys' release policies and their storage. |
| **TDC** | Training Data Consumer. Proposes contracts, supplies recipes, receives outputs. |
| **CCRP** | Clean-room provider. Optionally operates compute on behalf of a TDC. Untrusted. |
| **CCR** | Confidential clean room: one time-bound TEE workload executing one *run* of one contract. |
| **KMS** | `depa-kms`. CCF app on Azure Confidential Ledger (ACL). Dataset keys, release policies, OIDC issuer, CCR release registry. |
| **CS** | `depa-contract-service`. CCF app on ACL. Parties, dataset catalog, contracts, runs, execution receipts. |

Platforms (`platform` field everywhere):

| Value | TEE | Evidence |
|---|---|---|
| `aci-snp` | Azure Confidential ACI (via AKS virtual nodes v2), AMD SEV-SNP | Raw SNP report + endorsements, verified in CCF with `snp_attestation.verifySnpAttestation` |
| `gcp-cs-tdx` | GCP Confidential Space on C3 (Intel TDX) | Google Cloud Attestation OIDC token (`iss = https://confidentialcomputing.googleapis.com`), validated by CCF JWT auth (issuer registered on the ACL instance) |
| `virtual` | none (development only) | Unsigned JSON. Accepted only if governance setting `allow_virtual_attestation = true` **and** the dataset policy lists `virtual`. MUST never be enabled for production datasets. |

---

## 2. Encodings and primitives

- **Canonical JSON**: RFC 8785 (JCS). `digest(x)` = `"sha256:" + hex(SHA-256(JCS(x)))`.
  Python: package `rfc8785`. TypeScript: sorted-key serializer with ES number formatting.
- **base64url** without padding unless stated otherwise. Hex is lowercase.
- **Signing keys (parties, services)**: ECDSA P-256 / SHA-256 (`ES256`). Public keys are JWK
  (`kty=EC, crv=P-256, x, y, kid`). P-384/`ES384` MAY be accepted.
- **Signed object (JWS)**: JWS Compact Serialization. Protected header MUST contain
  `alg`, `kid`, `typ`. ECDSA signatures are raw `r||s` (JWS rules); convert to DER where the
  crypto API requires it.
- **HPKE** (RFC 9180), base mode, suite:
  KEM `DHKEM(X25519, HKDF-SHA256)` (0x0020), KDF `HKDF-SHA256` (0x0001), AEAD `AES-256-GCM` (0x0002).
  Same suite as the DEPA-inferencing / Privacy Sandbox KMS.
- **Key wrapping KMS→CCR**: `RSA-OAEP` with SHA-256, CCR-generated RSA-3072 key pair,
  public key supplied as PEM (`wrappingKey`). `wrapping_key_sha256` = hex SHA-256 of the exact
  PEM string bytes sent.
- **Time**: RFC 3339 UTC strings in documents; NumericDate (seconds) in JWT/JWS claims.

---

## 3. Identity and request authentication

### 3.1 Parties

A party is registered (by DC governance) in **both** services with the same record:

```json
{
  "party_id": "bank-a",                 // [a-z0-9][a-z0-9-]{1,62}
  "name": "Bank A Ltd",
  "roles": ["tdp"],                     // subset of tdp, tdc, ccrp, dc-member, auditor
  "signing_keys": [ { "kty":"EC","crv":"P-256","x":"…","y":"…","kid":"k1" } ],
  "hpke_public_keys": [ { "kid":"h1", "x25519":"<base64url 32 bytes>" } ],  // to receive outputs (optional)
  "status": "active"                    // active | suspended
}
```

A key is referenced as `"<party_id>#<kid>"`.

### 3.2 Signed requests (all party- and member-initiated mutations)

ACL user management is not used for parties. Mutating party/member endpoints use
`authn_policies: ["no_auth"]` and carry a **signed request** body:

```json
{ "jws": "<compact JWS>" }
```

Protected header: `{ "alg":"ES256", "kid":"bank-a#k1", "typ":"depa-request+jws" }`.
Payload:

```json
{
  "aud": "depa-kms" | "depa-contract-service",
  "op":  "<operation name, see API tables>",
  "iat": 1764000000,
  "jti": "<uuid4>",
  "body": { … operation-specific … }
}
```

Services MUST: verify the signature with the registered, active key; require `aud` = own
service name and `op` = the endpoint's operation; reject `|now - iat| > 300 s`; reject a
`jti` seen before (replay table, entries MAY be pruned after 10 min); check the party's roles
for the operation. The raw JWS MUST be stored with the resulting state change (audit).

### 3.3 Operator bootstrap

The ACL *Administrator* (DC operator identity) deploys the app bundle
(`PUT /app/userDefinedEndpoints`) and calls `POST /gov/bootstrap` exactly once
(authn: ACL user identity, `user_cose_sign1` or JWT as supported by ACL; on a plain CCF sandbox
`member_cert`/`user_cert`). After bootstrap succeeds the endpoint MUST permanently fail.
Bootstrap body: `{ "members": [Party…], "threshold": {"default": "majority"},
"settings": { "base_url": "https://…", "allow_virtual_attestation": false } }`. `base_url` is the
service's public base URL: the GCP attestation audience and the `iss` of RATs and receipts.

**Every signed body names its target resource** (e.g. `proposal_id`, `dataset_id`,
`contract_id`) even when the path already does, so that an intercepted signature cannot be
redirected to another resource.

Residual risk (documented in the threat model): the ACL Administrator can replace the app
bundle. Mitigations: the app records `digest(bundle)` it was built from at `GET /info`; the
ledger records every bundle update; the CLI `depa dc audit` compares the deployed bundle with
the reproducible-build digest approved by governance (`approved_app_digests`).

---

## 4. Governance (both services, app-level, identical semantics)

CCF member governance is not available on ACL, so governance is implemented in the app.

- `POST /gov/proposals` — signed by a `dc-member`; `op = "gov.propose"`;
  body `{ "actions": [ {"name": "...", "args": {...}} ], "expires_in_s": 604800 }`.
  Returns `{ proposal_id, state: "open" }`. `proposal_id = digest(payload)`.
- `POST /gov/proposals/{id}/ballots` — signed by a `dc-member`; `op = "gov.vote"`;
  body `{ "proposal_id": "…", "vote": "approve" | "reject" }`. One ballot per member (last one
  wins while open). If an action can no longer be applied when the threshold is reached, the
  ballot is rolled back (409) and the proposal stays open.
- A proposal is **accepted** and applied atomically in the same transaction as the ballot that
  reaches the threshold; **rejected** once acceptance becomes impossible; **expired** after
  `expires_in_s`. `GET /gov/proposals[/{id}]`, `GET /gov/members`, `GET /gov/constitution`.
- Threshold: `"majority"` (> 50 % of active members), `"unanimous"`, or `{"n": k}`. Per-action
  overrides in `constitution.thresholds`. Defaults: `set_member`, `remove_member`,
  `set_constitution`, `set_settings`: **unanimous**; everything else: **majority**. `{"n": k}`
  must not exceed the number of active members. `set_party` cannot grant or change the
  `dc-member` role; membership changes only through `set_member` / `remove_member`.

Common actions (both services):

| Action | Args |
|---|---|
| `set_member` / `remove_member` | Party (role `dc-member`) / `{party_id}` |
| `set_party` / `remove_party` | Party / `{party_id}` |
| `set_constitution` | `{ thresholds: {action: threshold}, default: threshold }` |
| `set_peer_service` | `{ name: "depa-kms"|"depa-contract-service", url, jwks: [JWK] }` — pins the peer's signing keys |
| `set_jwt_validation_policy` | `{ issuer, claims: { <claim>: value | [values] } }` (as in the inferencing KMS) |
| `set_settings` | `{ allow_virtual_attestation: bool, trust_bundle_digest: "sha256:…", base_url, … }` |
| `approve_app_digest` | `{ digest }` |

| `approve_ccr_release` / `revoke_ccr_release` | CCR release record (§6) / `{release_id}`. Kept in **both** services; each checks measurements independently. The CLI submits identical proposals to both. |

KMS-only actions:

| Action | Args |
|---|---|
| `set_oidc_settings` | `{ issuer_url, token_ttl_s (≤ 900), allowed_audiences_global: [..] }` |
| `suspend_dataset` / `resume_dataset` | `{dataset_id, reason}` — DC may only **tighten**: it can suspend, never relax a TDP policy. `resume_dataset` only lifts a DC suspension. |

CS-only actions: `revoke_contract` `{contract_id, reason}`.

**Invariant:** governance can never modify a TDP's dataset policy, dataset keys, or a contract's
content. TDP-owned state changes only via that TDP's signed requests.

---

## 5. Service signing keys and peer verification

Each service generates on bootstrap an ES256 key pair inside CCF (private key in a private
table, never exported). `GET /info` returns:

```json
{ "service": "depa-kms", "version": "2.0.0", "bundle_digest": "sha256:…",
  "jwks": [JWK], "oidc_issuer": "https://…" /*KMS only*/ }
```

The KMS pins the CS's JWKS (and vice versa) through `set_peer_service`. Peer-signed tokens are
verified only against pinned keys.

---

## 6. CCR releases (KMS registry)

A *release* is one measured CCR image + deployment template for one platform.

```json
{
  "release_id": "depa-ccr-torch-2.0.0-aci",
  "platform": "aci-snp",
  "families": ["torch"],                 // trainer families inside (classic | torch | llm | custom)
  "tiers": [1, 2],
  "measurements": {
    // aci-snp:
    "host_data": ["<hex sha256 of CCE policy>"],
    "uvm": { "did": "did:x509:…", "feed": "ContainerPlat-AMD-UVM", "min_svn": "…" }
    // gcp-cs-tdx:
    // "image_digests": ["sha256:…"], "support_attributes": ["STABLE"], "require_debug_disabled": true
    // virtual:
    // "virtual_ids": ["virtual-dev"]
  },
  "provenance": { "source": "https://github.com/prescera/depa-training", "commit": "…", "image": "…@sha256:…", "build": "<url>" }
}
```

`measurement_id` (normalized): `aci-snp` → `host_data` hex; `gcp-cs-tdx` → container image
digest; `virtual` → virtual id.

`release_id` is immutable: approving an existing `release_id` with different content MUST be
rejected; a revoked `release_id` can never be re-approved. Contracts name releases by id, so a
DC majority cannot substitute the code behind a release that TDPs already signed for.

Tier-3 (custom code) CCRs are not in the registry; they are authorized per contract (§8).

---

## 7. Datasets

### 7.1 KMS dataset records (keys and release policy — owned by the TDP)

| Endpoint | Auth | op | Description |
|---|---|---|---|
| `POST /datasets` | signed (owner, role tdp or tdc) | `dataset.create` | body `{dataset_id, description}`. Creates HPKE X25519 key pair `kid = "<dataset_id>#1"`. |
| `GET /datasets/{owner}/{name}` | none | — | `{dataset_id, owner, keys:[{kid, x25519, created_at, state}], policy, state}` |
| `PUT /datasets/{owner}/{name}/policy` | signed (owner) | `dataset.set_policy` | body `{dataset_id, policy}` (Release Policy below). Versioned; history at `GET …/policy/history`. |
| `POST /datasets/{owner}/{name}/rotate` | signed (owner) | `dataset.rotate` | body `{dataset_id, retire_previous?}`. New key version `#n+1`; old private keys remain releasable for existing ciphertexts unless `retire_previous: true`. |
| `POST /datasets/{owner}/{name}/suspend` · `/resume` | signed (owner) | `dataset.suspend` · `dataset.resume` | body `{dataset_id, reason?}`. Owner kill switch, immediate. A DC suspension cannot lift an owner suspension. |

`dataset_id`: `<owner_party_id>/<name>` with name `[a-z0-9][a-z0-9._-]{0,54}`. In URL paths it
occupies two segments. The length limit keeps the OIDC subject `dataset:<dataset_id>` within
GCP's 127-byte `google.subject` limit.

**Release Policy v1** (all present conditions MUST hold; absent = most restrictive default):

```json
{
  "version": 1,
  "allowed_platforms": ["aci-snp", "gcp-cs-tdx"],      // default: []  (nothing)
  "allowed_tiers": [1, 2],                              // default: [1]
  "allowed_tdcs": ["*"],                                // party ids or "*"; default: []
  "allowed_purposes": ["TRAINING"],
  "privacy": { "require_dp": true, "max_epsilon": 5.0, "max_delta": 1e-5 },   // optional
  "allowed_output_modes": ["tdc", "tee-only"],          // default: ["tee-only"]
  "allowed_audiences": ["sts.amazonaws.com"],           // OIDC token audiences for this dataset's storage
  "allowed_custom_measurements": [],                    // tier-3 measurement ids explicitly accepted
  "max_runs_per_contract": 10,                          // optional
  "not_after": "2027-12-31T00:00:00Z"                   // optional
}
```

### 7.2 CS catalog (dataset descriptors — discovery and binding)

`POST /catalog/datasets` signed by owner, `op = "catalog.publish"`, body = Dataset Descriptor:

```json
{
  "dataset_id": "bank-a/credit-applications",
  "version": "2026-11-01",                              // immutable once published
  "name": "Credit applications", "description": "…",
  "format": "dedf/1",
  "manifest_digest": "sha256:…",                        // §7.3
  "schema": { "type": "tabular", "files": ["*.parquet"], "columns": [ {"name":"SK_ID_CURR","type":"int64","pii":"pseudonymous"} ] },
  "location": { "connector": "s3" | "azure-blob" | "gcs" | "https" | "file",
                "uri": "s3://bucket/prefix/", "region": "ap-south-1" },
  "federation": {                                       // how a CCR obtains read access (§10)
    "aws":   { "role_arn": "arn:aws:iam::…:role/…" },
    "gcp":   { "audience": "//iam.googleapis.com/projects/…/locations/global/workloadIdentityPools/…/providers/…" },
    "azure": { "tenant_id": "…", "client_id": "…" }
  },
  "kms": { "url": "https://…", "kid": "bank-a/credit-applications#1" },
  "size_bytes": 123, "records": 456
}
```

`GET /catalog/datasets`, `GET /catalog/datasets/{owner}/{name}`,
`GET /catalog/datasets/{owner}/{name}/versions/{v}`.
The CS MUST verify the signer is the `dataset_id` owner prefix.

### 7.3 Encrypted dataset format `dedf/1`

Storage layout under the dataset prefix:

```
manifest.json          # Manifest (plaintext, public metadata only)
manifest.jws           # detached-payload JWS by the owner over JCS(manifest)  (typ "depa-manifest+jws")
objects/<path>.dedf    # one encrypted object per plaintext file; <path> is the relative path
```

Manifest:

```json
{
  "format": "dedf/1",
  "dataset_id": "bank-a/credit-applications", "version": "2026-11-01",
  "owner": "bank-a", "created_at": "…",
  "segment_size": 1048576,
  "wrapped_dek": { "kid": "bank-a/credit-applications#1", "enc": "<b64url>", "ct": "<b64url>" },
  "files": [ { "path": "app.parquet", "plaintext_size": 1, "ciphertext_size": 2, "ciphertext_sha256": "<hex>" } ]
}
```

- `manifest_digest = digest(manifest)`.
- DEK: 32 random bytes per dataset version. `wrapped_dek` = HPKE `SealBase(pkR = dataset key
  kid, info = "depa-training/dedf/1/dek|" + dataset_id + "|" + version, aad = "", pt = DEK)`.
- Per-file key: `K_f = HKDF-SHA256(ikm = DEK, salt = file_salt, info = "depa-training/dedf/1/file|" + path, L = 32)`.
- Object = `header || segments`. Header (38 bytes): magic `"DEDF1\0"` (6) · version `0x01` (1) ·
  segment_size u32 BE (4) · file_salt (16) · nonce_prefix (7) · reserved zero bytes (4).
  Total header length: 38 bytes.
- Segment `i` (0-based): `AES-256-GCM(K_f, nonce = nonce_prefix || u32be(i) || last_flag, aad = SHA-256(header), pt_i)`,
  `last_flag = 0x01` on the final segment else `0x00`. All segments except the last carry exactly
  `segment_size` plaintext bytes; the last carries 0..segment_size bytes (an empty file has one
  empty last segment). Decryption MUST fail on a missing last segment, extra data, reordering
  or any tag failure.
- Readers MUST check `ciphertext_sha256` and the manifest JWS, and that `manifest_digest`
  equals the value bound in the contract.
- Outputs produced by a CCR use the same format with `dataset_id = "<tdc>/outputs/<contract_id>/<run_id>"`
  and `version = <output name>`. `wrapped_dek` is replaced by
  `wrapped_deks: [ {recipient, kid, enc, ct} ]` (one per recipient; recipient is
  `party:<id>#<kid>` or `kms:<output key kid>`). The output manifest JWS is signed with the run
  signing key (`kid = "run:<run_id>"`), which recipients verify against the run record at the CS.
  Outputs are uploaded under `<output.location.uri><contract_id>/<run_id>/<name>/`, manifest last.

---

## 8. Contracts (CS)

### 8.1 Contract document `depa-training/contract/v2`

```json
{
  "schema": "depa-training/contract/v2",
  "contract_id": "<uuid4>",
  "title": "Credit risk model 2026", "description": "…",
  "purpose": "TRAINING",
  "parties": { "tdc": "fintech-x", "tdps": ["bank-a", "bank-b"], "ccrp": "vendor-y" },
  "validity": { "not_before": "…", "not_after": "…" },
  "datasets": [ { "dataset_id": "bank-a/credit-applications", "version": "2026-11-01",
                  "manifest_digest": "sha256:…" } ],
  "computation": {
    "tier": 1,
    "platforms": ["aci-snp", "gcp-cs-tdx"],
    "ccr_releases": ["depa-ccr-classic-2.0.0-aci", "depa-ccr-classic-2.0.0-gcp"],  // tier 1/2
    "custom_ccr": null,         // tier 3: { platform, measurement_id, image, source, review_report_digest }
    "recipes": [ { "digest": "sha256:…", "document": { /* recipe, see trainers spec */ } } ],
    "max_runs": 5,
    "max_duration_minutes": 240
  },
  "privacy": { "mode": "dp" | "none", "epsilon": 4.0, "delta": 1e-5 },
  "output": {
    "mode": "tdc" | "tee-only",
    "recipients": [ "party:fintech-x#h1" ],             // mode tdc
    "allowed_formats": ["safetensors", "onnx", "xgboost-json", "skops", "keras"],
    "max_bytes": 2147483648,
    "metrics_allowlist": ["accuracy", "roc_auc", "loss"],
    "location": { "connector": "azure-blob", "uri": "https://acct.blob.core.windows.net/out/",
                  "federation": { "azure": {"tenant_id": "…", "client_id": "…"} } }
  },
  "terms": { "legal_text_digest": "sha256:…", "uri": "https://…", "governing_law": "IN" }
}
```

`contract_digest = digest(contract)`. Recipe `digest = digest(document)` and MUST match.
`terms.legal_text_digest = "sha256:" + hex(SHA-256(exact bytes of the legal text))`. When
`terms.uri` is set, `depa contract verify` downloads the text and checks the digest before signing.

### 8.2 Lifecycle

`draft → (all required signatures) → active → (revoked | expired | exhausted)`.
Required signers: `parties.tdc` and the owner of every dataset. Superseding = a new contract
whose `description` references the old one; there is no in-place amendment.

| Endpoint | Auth | op | Notes |
|---|---|---|---|
| `POST /contracts` | signed (tdc) | `contract.propose` | body = contract. CS validates schema, parties/roles, datasets exist in catalog with matching `manifest_digest`, recipe digests. State `draft`. |
| `GET /contracts/{id}` | none | — | `{contract, contract_digest, state, signatures:[{party, jws, at}], runs_used}` |
| `GET /contracts?party=<id>` | none | — | list |
| `POST /contracts/{id}/signatures` | signed (required party) | `contract.sign` | body `{contract_digest}`. Becomes `active` when the last required signature lands and `now ≥ not_before`. |
| `POST /contracts/{id}/revoke` | signed (any required party) or governance | `contract.revoke` | body `{contract_id, reason}`. Immediate for new runs. RATs already issued stay valid at the KMS until they expire (≤ 300 s). |

---

## 9. Runs, binding, attestation

### 9.1 Run binding

The CCR generates, in TEE memory: `run_id` (uuid4), an RSA-3072 wrapping key pair, and an
ES256 *run signing key* pair. Binding object:

```json
{ "v": 1, "contract_id": "…", "contract_digest": "sha256:…", "run_id": "…",
  "recipe_digest": "sha256:…", "platform": "aci-snp",
  "wrapping_key_sha256": "<hex>", "run_key": { JWK },
  "trust_bundle_digest": "sha256:…" }
```

`binding_hash = SHA-256(JCS(binding))` (32 bytes).

**Trust bundle.** The CCR's trust anchors for the DC services:
`{ "v": 1, "cs": {"url", "service_cert_pem", "jwks"}, "kms": {"url", "service_cert_pem", "jwks"} }`,
supplied as `DEPA_TRUST_BUNDLE` (base64url JSON). `trust_bundle_digest = digest(bundle)`.
Governance sets the expected value in both services (`set_settings.trust_bundle_digest`;
`depa dc setup` computes it from the services' `/info` and TLS certificates). For every
non-`virtual` platform, both services MUST reject a binding whose `trust_bundle_digest` differs
from the setting, and MUST reject non-virtual runs if the setting is unset. Because the digest is
inside the attested binding, an operator who substitutes the trust anchors (for example to
man-in-the-middle the CCR and swap output recipient keys) never obtains data keys. This holds on
every platform, including those whose measurement covers only the image.

### 9.2 Attestation envelope (request field `attestation`)

```json
{ "platform": "aci-snp", "evidence": "<b64>", "endorsements": "<b64>",
  "uvm_endorsements": "<b64>", "endorsed_tcb": "<hex>" }
{ "platform": "gcp-cs-tdx" }       // token travels as `Authorization: Bearer <GCA token>`
{ "platform": "virtual", "binding_hash": "<hex>", "virtual_id": "virtual-dev" }
```

Binding in evidence:
- `aci-snp`: `REPORT_DATA[0..32] = binding_hash`, `REPORT_DATA[32..64] = 0`.
- `gcp-cs-tdx`: token requested from the launcher (`/run/container_launcher/teeserver.sock`,
  `POST /v1/token`, `token_type: "OIDC"`) with `audience = <service base URL>` and
  `nonces = [hex(binding_hash)]`; the service requires `hex(binding_hash) ∈ eat_nonce`.
- `virtual`: `binding_hash` field.

Additional fail-closed checks: `aci-snp` guests with debug enabled are rejected; GCA tokens
must have `secboot = true` and be covered by a governance JWT validation policy.

Verification (shared module, identical in KMS and CS) yields normalized claims
`{platform, measurement_id, binding_hash, debug, raw}` and MUST check:
- `aci-snp`: `verifySnpAttestation` succeeds; UVM endorsement `did`/`feed`/`svn ≥ min_svn`
  match the release; `host_data` = measurement_id.
- `gcp-cs-tdx`: caller authenticated by CCF JWT policy; `iss` as above; `aud` = own base URL;
  `hwmodel = GCP_INTEL_TDX`; `swname = CONFIDENTIAL_SPACE`; `dbgstat = disabled-since-boot`;
  `submods.confidential_space.support_attributes ⊇ release.support_attributes`;
  `measurement_id = submods.container.image_digest`.
- `virtual`: setting `allow_virtual_attestation` is true.
Then the service recomputes `binding_hash` from the supplied `binding` and requires equality.

### 9.3 Run authorization — `POST /contracts/{id}/runs` (CS)

Body `{ binding, attestation }`. In **one transaction** the CS MUST check: attestation valid;
binding matches; contract `active`, `now` within validity, not revoked; `runs_used < max_runs`;
`recipe_digest ∈ computation.recipes`; `platform ∈ computation.platforms`; and either
(tier 1/2) `measurement_id` belongs to an approved, non-revoked release in the CS's own
registry that is also listed in `computation.ccr_releases`, or (tier 3)
`measurement_id = custom_ccr.measurement_id`.
It then increments `runs_used`, stores the run `{run_id, binding_hash, run_key, measurement_id,
platform, state: "authorized", deadline = now + max_duration_minutes}` and returns:

```json
{ "run_id": "…", "rat": "<JWS>" }
```

Transaction ids are never in response bodies (a CCF handler cannot know its own transaction id);
clients read the `x-ms-ccf-transaction-id` response header and fetch receipts with
`GET /receipt?transaction_id=`.

**Run Authorization Token (RAT)**: JWS `typ = "depa-rat+jwt"`, signed with the CS key, claims:

```json
{ "iss": "<CS base URL>", "aud": "depa-kms", "sub": "<run_id>", "iat": …, "exp": iat + 300,
  "contract_id": "…", "contract_digest": "sha256:…", "run_id": "…",
  "binding_hash": "<hex>", "platform": "aci-snp", "measurement_id": "…", "tier": 1,
  "custom_measurement": false, "ccr_releases": ["…"], "recipe_digest": "sha256:…",
  "tdc": "fintech-x", "purpose": "TRAINING",
  "datasets": [ { "dataset_id": "…", "version": "…", "manifest_digest": "sha256:…" } ],
  "privacy": { "mode": "dp", "epsilon": 4.0, "delta": 1e-5 },
  "output": { "mode": "tdc", "recipients": ["party:fintech-x#h1"] },
  "deadline": … }
```

### 9.3a RAT refresh — `POST /contracts/{id}/runs/{run_id}/rat` (CS)

Runs outlive the 300 s RAT, but output tokens and output keys are requested at the end of a run.
Body `{ "jws": … }` signed with the run signing key (`typ = "depa-run-event+jws"`), payload
`{ "op": "run.refresh_rat", "contract_id", "run_id", "iat", "jti" }` (iat window and jti replay
rules as in §3.2). The CS MUST check: the run exists and is neither `completed` nor `failed`;
`now < deadline`; the contract is still `active` (not revoked or expired). It returns a fresh RAT
with the same claims and `exp = min(now + 300, deadline)`. A refresh does not consume a run.
Because each refresh re-checks contract state, revocation also stops in-flight runs at their next
refresh.

### 9.4 Key release — `POST /keys/release` (KMS) — the single atomic check

Body `{ binding, attestation, rat, dataset_id, kids?: [..], wrappingKey: "<PEM>" }`.
In **one transaction** the KMS MUST check:

1. attestation valid (§9.2) and `binding_hash` matches the supplied binding;
2. `SHA-256(wrappingKey PEM) = binding.wrapping_key_sha256`;
3. RAT signature valid under the pinned CS JWKS, `aud = depa-kms`, not expired;
4. `rat.binding_hash = binding_hash`, `rat.measurement_id = attested measurement_id`,
   `rat.platform = attested platform`, `rat.run_id = binding.run_id`;
5. measurement: (tier 1/2) belongs to an **approved, non-revoked** release in the KMS registry
   whose `release_id ∈ rat.ccr_releases` and `tiers ∋ rat.tier`;
   (tier 3) `rat.custom_measurement` and `measurement_id ∈ policy.allowed_custom_measurements`;
6. `dataset_id ∈ rat.datasets`; dataset not suspended (by owner or DC); keys requested exist;
7. Release Policy: platform, tier, tdc, purpose, privacy (`rat.privacy.mode = "dp"` and
   `epsilon ≤ max_epsilon`, `delta ≤ max_delta` when `require_dp`), output mode,
   `not_after`, `max_runs_per_contract` (KMS-side counter of distinct run_ids per contract);
8. record the release event `{run_id, contract_id, dataset_id, kids, measurement_id, at}`.

Response:

```json
{ "keys": [ { "kid": "…", "wrapped": "<b64 RSA-OAEP-SHA256(raw 32-byte X25519 private key)>" } ],
  "release_receipt": "<JWS typ depa-key-release+jwt signed by the KMS key: {iss, release_id, run_id, contract_id, dataset_id, kids, binding_hash, platform, measurement_id, iat}>" }
```

The release record is retrievable at `GET /key-releases/{release_id}`. `wrappingKey` MUST be an
RSA key of at least 3072 bits.

### 9.5 Run events and execution receipts (CS)

`POST /contracts/{id}/runs/{run_id}/events` body `{ "jws": … }` signed with the **run
signing key** registered in the binding (`typ = "depa-run-event+jws"`), payload
`{ run_id, seq, type: "started" | "progress" | "completed" | "failed", at, data }`.
`seq` strictly increasing. After `deadline` only `failed`/`completed` are accepted, once.

`completed.data` is the **Execution Receipt**:

```json
{
  "run_id": "…", "contract_id": "…", "contract_digest": "…", "recipe_digest": "…",
  "platform": "…", "measurement_id": "…", "started_at": "…", "finished_at": "…",
  "inputs": [ { "dataset_id": "…", "version": "…", "manifest_digest": "…", "release_receipt": "<JWS>" } ],
  "privacy_spent": { "mode": "dp", "epsilon": 3.97, "delta": 1e-5, "accountant": "rdp" },
  "outputs": [ { "name": "model", "format": "safetensors", "manifest_digest": "sha256:…",
                 "location": "…", "recipients": ["party:fintech-x#h1"] } ],
  "metrics": { "accuracy": 0.91 },
  "status": "succeeded"
}
```

The CS MUST verify every `release_receipt` against the pinned KMS JWKS, that its
`run_id`/`binding_hash` match the run, and that there is one for every contract dataset
(a CCR that never obtained keys cannot claim success), then store the receipt and return
`{ txid, receipt_jws }` where `receipt_jws` is the CS-signed receipt (`typ = "depa-exec-receipt+jwt"`).
`GET /runs/{run_id}` returns run state, events and receipt; `GET /receipt?transaction_id=`
returns the CCF receipt proving inclusion in the ledger.

---

## 10. KMS as OIDC issuer (storage federation)

- `GET /oidc/.well-known/openid-configuration`, `GET /oidc/jwks` — public. The issuer URL is
  `set_oidc_settings.issuer_url` and MUST be reachable with a publicly trusted TLS certificate
  (Application Gateway in front of ACL, as in DEPA-inferencing). The JWKS is also exposed by
  `GET /info` so that monitors can detect a gateway serving a different key set.
- `POST /oidc/token` body `{ binding, attestation, rat, resource, audience }` where `resource`
  is `{"dataset_id": "…"}` or `{"output": true}`. The KMS performs checks 1–6 of §9.4 (for
  `output`: `rat` valid and attestation matches) plus `audience ∈ policy.allowed_audiences`
  (dataset) or `audience ∈ allowed_audiences_global` (output), then issues:

```json
{ "iss": "<issuer_url>", "sub": "dataset:bank-a/credit-applications" | "output:fintech-x",
  "aud": "<audience>", "iat": …, "nbf": …, "exp": iat + token_ttl_s, "jti": "…",
  "contract_id": "…", "run_id": "…", "platform": "…", "measurement_id": "…" }
```

  Signed `ES256` with a P-256 key generated inside CCF. The CCF 6 JS runtime supports
  RSA-PSS, ECDSA, EdDSA and HMAC but not RSASSA-PKCS1-v1_5, so `RS256` is not available.
  AWS STS and GCP Workload Identity Federation accept ES256. Microsoft Entra federated
  credentials document RS256 only, so Azure Blob federation needs either RS256 support in CCF
  (requested upstream) or the interim path in §10.1. `exp` never exceeds the run deadline.
  `sub` is stable per
  dataset so a TDP configures its cloud trust **once**:
  - **AWS**: IAM OIDC provider for `issuer_url`; role trust policy
    `StringEquals {"<issuer>:aud": "sts.amazonaws.com", "<issuer>:sub": "dataset:<id>"}`;
    CCR calls `AssumeRoleWithWebIdentity`.
  - **GCP**: Workload Identity Pool + OIDC provider (issuer, allowed audience, `attribute.sub = assertion.sub`,
    condition on `assertion.sub`); grant `roles/storage.objectViewer` to
    `principal://iam.googleapis.com/projects/<n>/locations/global/workloadIdentityPools/<pool>/subject/dataset:<id>`;
    CCR exchanges via STS (`urn:ietf:params:oauth:grant-type:token-exchange`).
  - **Azure**: app registration / user-assigned identity with a federated credential
    (`issuer`, `subject = dataset:<id>`, `audience = api://AzureADTokenExchange`); RBAC
    *Storage Blob Data Reader*; CCR uses the client-assertion flow.

### 10.1 Interim Azure Blob path: TDP-operated SAS broker

Until RS256 is available in CCF, an Azure-hosted TDP runs a small **federation broker** in its
own subscription (Azure Function with a managed identity; Terraform module
`deploy/terraform/modules/azure/federation-broker`). It lives in the TDP's own trust domain, so
no third party is added.

- The CCR calls `POST <broker_url>/sas` with `Authorization: Bearer <KMS OIDC token>`
  (audience = the broker's configured audience).
- The broker verifies the ES256 token against the KMS JWKS (pinned at deployment, refreshed only
  from the configured issuer), plus `iss`, `aud`, `exp`/`nbf`, and that `sub` is one of its
  configured subjects (`dataset:<id>` or `output:<party>`).
- It returns a **user-delegation SAS** scoped to that dataset's container/prefix: read+list for
  datasets, create+write for outputs, HTTPS only, expiring no later than the token, and logged.
- The descriptor's federation block is `"azure": { "broker_url": "https://…", "audience": "…" }`.

When CCF gains RSASSA-PKCS1-v1_5, TDPs switch to native Entra federated credentials without
any change to clean rooms beyond the descriptor.

---

## 11. Output encryption

- Mode `tdc`: the CCR seals the output DEK to each recipient's registered HPKE public key
  (`hpke_public_keys` of the party in the CS registry, as listed in the contract).
- Mode `tee-only`: the CCR obtains a per-contract output public key from the KMS
  (`POST /outputs/{contract_id}/key` with `{binding, attestation, rat}`) and seals to it.
  The private key is releasable only via `POST /outputs/{contract_id}/release` to an attested
  workload whose measurement is in a release with `families ∋ "inference"` approved by
  governance (DEPA-inferencing integration point). It is never released to a party. The
  release request's binding is
  `{ "v": 1, "purpose": "depa-output-release", "contract_id", "platform", "wrapping_key_sha256", "nonce" }`.

---

## 12. Clean-room runtime contract

Inputs (non-secret, operator-supplied environment, not trusted):
`DEPA_CONTRACT_ID`, `DEPA_RECIPE_DIGEST`, `DEPA_CS_URL`, `DEPA_KMS_URL`, `DEPA_PLATFORM`,
`DEPA_TRUST_BUNDLE` (mandatory on non-virtual platforms; its digest is bound in the attested
binding and checked by both services, §9.1).
Everything security-relevant is re-derived from the contract fetched from the CS and checked by
CS/KMS at authorization; a malicious operator can at worst make the run fail. The CCR verifies
RATs and release receipts against the pinned JWKS, and refreshes its RAT (§9.3a) before
requesting output tokens or keys.

Scratch: plaintext lives only under `/depa`, which MUST be memory-backed or encrypted with an
ephemeral in-TEE key: on `aci-snp`, release CCE policies set `allow_unencrypted_scratch: false`;
on `gcp-cs-tdx`, the submitter mounts a tmpfs at `/depa` (`tee-mount`, allowed by the image
launch policy `tee.launch_policy.allow_mount_destinations`).

Tier 3: custom code MUST run with network isolation; if isolation is unavailable on the platform,
the run fails closed.

Execution:
1. fetch contract; require `active` and `digest` match; select recipe by digest; validate the
   recipe against the contract (tier, privacy, outputs, datasets) — fail closed;
2. generate run material; build binding; obtain evidence; `POST /contracts/{id}/runs`;
3. per dataset: `POST /keys/release`; `POST /oidc/token`; exchange with the cloud STS; download
   manifest, verify JWS + digest; stream-decrypt objects into an in-memory filesystem;
4. run the trainer as an unprivileged subprocess with a scrubbed environment (no tokens, no
   keys), inputs read-only, a single writable output directory, enforced `deadline`;
5. **output gate**: only declared outputs in allowed formats, total size ≤ `max_bytes`, metrics
   filtered to `metrics_allowlist`, privacy spent ≤ contract; then encrypt (`dedf/1`), upload
   via the output federation, emit `completed` with the Execution Receipt;
6. exit. stdout/stderr carry only structured, allowlisted status lines; production releases
   disable stdio (`--disable-stdio` on ACI; `log_redirect` debug-only launch policy on GCP).

Time bounds: Kubernetes `activeDeadlineSeconds` (AKS/VN2) or `--max-run-duration` with
`instance-termination-action=DELETE` (GCP), both ≤ `max_duration_minutes`, plus the CCR's own
watchdog and the CS run `deadline`.
