# depa-console: DEPA Training web console

A small, local-first web console for **one party** of a DEPA Training Data Collaborative. It is a
thin layer over the `depa` SDK (`sdk/python`): every check, signature and protocol step is the SDK's
own code, and the console adds views, forms and background progress. Party keys stay in the
party's key directory. The SDK reads them in the console process to sign; they are never sent to
the browser.

Stack: FastAPI with server-rendered Jinja2 templates, one CSS file and about 25 lines of vanilla
JavaScript. FastAPI was chosen because async server-sent events stream task progress without tying
up worker threads, while the blocking SDK calls run in its thread pool. There is no frontend
build step and nothing loads from a CDN.

## What it shows

The navigation shows only what the active party's roles allow (roles come from the party's
`party.json`).

| Page | Roles | What it does |
|---|---|---|
| Overview | all | Active party and keys (public ids only), environment (env file or profile, service URLs, pinned certificate fingerprints), KMS / CS health, version and app-bundle digest with governance approval, `dc audit` on demand, recent activity |
| Governance | dc-member | Members and constitution of both services, open and closed proposals with readable action details and ballots, approve / reject through a review-and-confirm page, and proposals to register a party, approve or revoke a clean-room release, or change the virtual-attestation setting (always sent identically to both services) |
| Datasets | tdp | Your datasets (catalog, local templates, contracts), KMS keys and state, current policy and its version history, a validated release-policy form with restrictive defaults, encrypt + upload + publish of a template's source directory with per-file encryption and per-object upload progress, and a prominent kill switch (suspend / resume with reason and confirmation) |
| Contracts | tdc, tdp | Contracts by state. TDCs can **propose** a contract: render a scenario's contract template (as `depa contract init`, optionally restricted to the virtual clean room for development) or paste / upload a contract in JSON or YAML; it is validated and shown before it can be proposed. A detail page that states the contract in plain language (who, dataset versions, purpose, platform and release, recipe steps, privacy, outputs, validity, runs used) with the result of `contract verify` for you. **Sign** is offered only when verification passes and re-verifies server-side before signing. Revoke needs a reason and a confirmation |
| Runs | tdc, tdp | Runs per contract, the event timeline, verification of the Execution Receipt and every KMS release receipt, ledger-inclusion check by transaction id, and for output recipients **fetch & decrypt** to a chosen local directory |
| Scenarios | all | Pick a scenario and platform, choose to act for yourself or for every party whose keys are held, and run the SDK's idempotent `scenario deploy` with line-by-line progress. Cancel stops it promptly, including a running local clean room or data-preparation script. With an environment file, `local`, `aks` and `gcp` are available as configured. With a plain development profile, the SDK derives an equivalent environment (file storage, the virtual release), generates and registers the scenario's parties, and only `local` runs |
| Activity | all | Background tasks with live logs (SSE, falling back to a refresh when JavaScript is off) and their results |

## Run it

```bash
pip install -e sdk/python -e gui
# against an environment file (Terraform outputs; see sdk/python/README.md)
depa-console --env depa-env.yaml --party icmr --scenarios-root scenarios
# or against a plain profile, e.g. the one `depa dev up` writes
depa-console --home ~/.depa
```

The console prints a one-time sign-in link such as `http://127.0.0.1:8400/login?token=…`. Open
it in a browser on the same machine. If keys for several parties are held (pilots and demos),
switch the active party from the header.

| Option | Purpose |
|---|---|
| `--home DIR` / `--env FILE` / `--party ID` | Profile, environment file and initial party (as for `depa`) |
| `--host` / `--port` | Bind address (default `127.0.0.1:8400`) |
| `--allow-remote` | Required for any non-loopback `--host`; prints a warning |
| `--tls-cert` / `--tls-key` | Serve HTTPS (use with `--allow-remote`) |
| `--scenarios-root DIR` | Where scenarios and dataset templates are found (default `scenarios`) |
| `--work-dir DIR` | Scenario working directories (default `<home>/console`) |
| `--storage-token-env NAME` / `--output-token-env NAME` | Names of environment variables holding bearer tokens for your dataset storage or the TDC output storage (azure-blob / gcs). The values are read only when needed and never shown |
| `--dev-trainer-cmd CMD` | Local scenario runs only: trainer command (development) |
| `--workers N` | Background workers (default 2) |

## Security

- **Loopback by default.** Any other bind address needs `--allow-remote`.
- **Sign-in token.** Only a browser that opened the printed link gets a session cookie
  (`HttpOnly`, `SameSite=Strict`, `Secure` under TLS). Other local users, other websites and
  DNS-rebinding pages therefore cannot drive the console. Request logging is off, so the token
  never reaches a log.
- **CSRF.** Every state-changing request needs a per-session HMAC token, a same-origin `Origin`
  (when sent) and a body of at most 1 MiB.
- **Headers.** A Host allowlist, plus a strict Content-Security-Policy (`script-src 'self'`, no
  inline script or style, no framing), `nosniff`, `no-referrer` and `no-store`, on every response.
- **Input.** All input is validated strictly: identifiers against the protocol grammars, choices
  against fixed lists, policies against field rules and then the Release Policy v1 schema, and
  pasted party or release records through the SDK's strict JSON and record parsers. Local paths
  must be absolute, and download targets must be new or empty.
- **Confirmations.** Destructive or binding actions (sign, revoke, suspend, revoke release,
  enable virtual attestation, ballots) need an explicit confirmation that the server enforces.
  Ballots go through a page that shows the proposal before you confirm.
- **Errors.** SDK exceptions become short messages. Bearer tokens, JWTs and signed-URL
  credentials are redacted from messages and task logs. Unexpected errors show a generic message,
  and the details go only to the terminal.
- **Bounded background work.** A fixed worker pool runs long operations, with a cap on active
  tasks, capped logs and pruned history, so no request blocks and no thread is unbounded.

## Development

```bash
pip install -e 'sdk/python[dev]' -e 'gui[dev]'
cd gui && pytest -q && ruff check src tests && ruff format --check src tests
```

The tests run the console with FastAPI's test client against the SDK's mock KMS and Contract
Service (`sdk/python/tests/mockdc.py`), served over pinned HTTPS. A session fixture writes an
environment file for four parties and completes one scenario run in a local virtual clean room.
The tests cover pages per role, sign-in and CSRF enforcement, policy-form validation, refusal to
sign when verification fails, contract proposal, the kill switch, governance ballots and
constitution, policy history and catalog versions, publishing with progress, receipt verification
and model retrieval, scenario deploy over SSE (with an environment file and in a development
profile) and its cancellation, and the task registry lifecycle.
