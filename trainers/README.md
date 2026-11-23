# depa-train: the DEPA Training v2 training runtime

`depa-train` runs inside a confidential clean room (CCR) on decrypted multi-party data. Code that
touches that data could leak it through the network, logs or model weights. So untrusted input is
reduced until it can be checked automatically:

| Tier | The consumer supplies | What is checked | Plugin(s) |
|---|---|---|---|
| 1 | A declarative **recipe** (configuration only) | JSON Schema per plugin, plus static checks: SQL policy, class/op allowlists, DP rules | every `*/…@1` plugin below |
| 2 | A **graph-only model**: ATen op graph + safetensors weights, no Python | every op checked against an allowlist; the harness owns data, optimizer, DP, checkpoints, outputs | `torch/graph-train@1` (experimental) |
| 3 | A **custom container**, approved by digest in the contract | same I/O, metrics and output contract; the code is reviewed out of band | `custom/entrypoint@1` |

Validation is fail-closed and finishes before any data is read. Plugins are vetted and versioned (`name@major`, semver), and are baked into a measured image. They are reviewed once per plugin version, not once per job.

## Interface (used by the CCR agent)

```
depa-train run --recipe <recipe.json|yaml> --inputs <dir> --outputs <dir> --result <result.json>
depa-train validate --recipe <file> [--contract <contract.json>] [--json]
depa-train plugins [--json]
depa-train schema [plugin]            # recipe JSON Schema, or a plugin's `with` schema
```

Exit codes: `0` success or valid · `1` run failed · `2` invalid recipe or usage error.

Python API (import-light: only `pydantic`, `jsonschema`, `pyyaml` and `sqlglot`; no framework is imported):

```python
from depa_train.api import validate_recipe, validate_against_contract
validate_recipe(recipe: dict) -> list[str]                        # [] = valid
validate_against_contract(recipe: dict, contract: dict) -> list[str]
```

`validate_against_contract` checks the following. Missing contract fields fail closed.
- **Tier:** must match `computation.tier`. Tier 3 requires `computation.custom_ccr`; tiers 1 and 2 require it to be null.
- **Privacy:** a `dp` contract requires a `dp` recipe with ε and δ at or below the contract's values. A `dp` recipe under a `none` contract is accepted, because it is stricter.
- **Outputs:** every output format must be in `output.allowed_formats`.
- **Inputs:** every input `dataset_id` must be in `datasets`.

### Directories

- `<inputs>/<alias>/` holds the decrypted, read-only files of each recipe input.
- `<outputs>` must be empty. Each declared output is written to `<outputs>/<name>/`, a flat directory holding at least one file in the declared format plus optional JSON metadata. Nothing else may exist under `<outputs>`.

### `result.json`

```json
{
  "status": "succeeded",
  "metrics": {"accuracy": 0.92, "test_loss": 0.53},
  "privacy_spent": {"mode": "dp", "epsilon": 0.994, "delta": 1e-05, "accountant": "opacus-rdp"},
  "outputs": [{"name": "model", "format": "onnx", "files": ["model.onnx"]}]
}
```

- **`outputs[].files` are paths relative to `<outputs>/<name>/`** (that output's own directory), not relative to `<outputs>`. Example: `<outputs>/model/model.onnx` is listed as `"model.onnx"`.
- `metrics` contains exactly the recipe's top-level `metrics` (all finite numbers). Metrics a step computes but the recipe does not declare are dropped.
- `privacy_spent` for `mode: none` is `{"mode": "none", "epsilon": null, "delta": null, "accountant": "none"}`. For `dp` it is the sum over training steps (basic composition). `accountant` names the method: `opacus-rdp`, `dp-xgboost:basic-composition+poisson-subsampling`, `custom:<name>`, or `basic-composition(a,b)` when several steps trained.
- On failure: `{"status": "failed", "error": "...", "metrics": {}, "outputs": [], "privacy_spent": …}`.
  - Everything the steps wrote is deleted.
  - `privacy_spent` reports any budget already consumed.
  - `error` carries messages from this package only. Third-party exceptions are reported by type, because their messages may contain data values.
- stderr carries one JSON status line per event (step start and finish, row counts, epochs, ε so far): identifiers and numbers only, never data values.

## Recipe (`depa-training/recipe/v1`)

```yaml
schema: depa-training/recipe/v1
name: covid-dp-mlp
tier: 1                       # 1 | 2 | 3
family: torch                 # classic | torch | llm | custom (= CCR image)
inputs:  [{alias: icmr, dataset_id: icmr/covid-test-results}, ...]
steps:
  - {id: join,  uses: sql/duckdb@1,     with: {tables: [...], query: "SELECT ...", privacy_unit: pk}}
  - {id: split, uses: tabular/split@1,  with: {source: {step: join}, target: y, ...}}
  - {id: train, uses: torch/train@1,    with: {data: {kind: tabular, step: split}, ..., save: {output: model, format: onnx}}}
  - {id: eval,  uses: eval/classification@1, with: {predictions: {step: train}, metrics: [accuracy, roc_auc]}}
privacy: {mode: dp, epsilon: 1.0, delta: 1.0e-5}
outputs: [{name: model, format: onnx}]       # safetensors | onnx | xgboost-json | xgboost-ubj | skops | keras
metrics: [accuracy, roc_auc, test_loss]
```

- A step uses an earlier step's artifact through `{step: <id>}` (artifact kinds: `table`, `tabular_splits`, `predictions`). Validation checks that the referenced step exists, comes earlier, and produces the right kind.
- Every declared output must be written by exactly one step, in the declared format. Every declared metric must be produced by exactly one step.
- **Seeds:** each step uses `with.seed`, or a seed derived from `sha256(recipe name, step id)`. DP noise and DP sampling are the exception (see below).
- The authoritative schemas are generated from the pydantic models and committed under `src/depa_train/schemas/` (`depa-train schema --write src/depa_train/schemas`). A test keeps them in sync.

## Plugin catalogue

| Plugin | Tier | Family | DP | Wraps / does |
|---|---|---|---|---|
| `sql/duckdb@1` | 1 | all | — | Joins and feature selection (replaces v1 Spark). One read-only query, checked by a sqlglot policy and run on a locked-down DuckDB. Optional `privacy_unit` uniqueness check. |
| `tabular/split@1` | 1 | all | — | Feature selection, missing values, one-hot/ordinal encoding, declared bounds (clipping), min-max/standard scaling, stratified train/val/test split |
| `sklearn/train@1` | 1 | classic | no | Allowlisted scikit-learn estimators and pipelines → **skops**; the output is verified to contain only trusted types |
| `xgboost/train@1` | 1 | classic | yes | xgboost `hist` (non-DP), or Sarus **dp-xgboost** `approxDP` → XGBoost JSON/UBJ |
| `torch/train@1` | 1 | torch | yes | ModelFactory (v1 config models) or allowlisted torchvision/timm/MONAI architectures. Losses, optimizers and schedulers from allowlists. **Opacus** DP-SGD with the RDP accountant, optional **Lightning** backend (non-DP). Output: safetensors or **ONNX** (dynamo exporter). |
| `keras/train@1` | 1 | torch | no | Keras 3 model from an allowlisted JSON config (`safe_mode=True`), torch backend by default, `jax` if installed → `.keras` |
| `hf/sft@1` | 1 | llm | experimental | LoRA SFT of a small causal LM on CPU with transformers + PEFT, harness-owned loop (per-sample completion-only loss), Opacus DP optional → `adapter_model.safetensors` + `adapter_config.json` |
| `torch/graph-train@1` | 2 | torch | yes | Trains a `depa-graph/v1` ATen graph (experimental). Functional per-sample DP-SGD via `torch.func`, accounted with the Opacus RDP accountant. |
| `custom/entrypoint@1` | 3 | custom | self-reported | Runs the declared argv in the approved image: no shell, scrubbed environment, deadline. Same output gate and checks as tiers 1–2. |
| `eval/classification@1`, `eval/regression@1`, `eval/segmentation@1` | 1 | all | — | Scalar held-out metrics from a training step's predictions: accuracy, F1, ROC-AUC, MSE/R², Dice, Jaccard, Hausdorff, … |

## Safety rules (enforced centrally)

- **No pickle anywhere.**
  - Inputs are read only as CSV, Parquet, JSONL, Arrow/Feather, HDF5 (numeric), safetensors, PNG/JPEG or NIfTI.
  - `.pkl/.pickle/.pt/.pth/.ckpt/.bin/.joblib/.npy/.npz` files are refused wherever they appear, including inside model directories.
  - Outputs are only safetensors, ONNX (no external data), XGBoost JSON/UBJ, skops (no untrusted types) and `.keras` (allowlisted config). The output gate checks structure: safetensors headers and offsets, the ONNX checker, skops trusted types, `.keras` archive members and config.
  - `torch.load` is never called, and `TORCH_FORCE_WEIGHTS_ONLY_LOAD=1` is set for every run.
- **Runtime guard** (CPython audit hook, irrevocable, installed by `depa-train run`):
  - Any unpickling of a class or function (`pickle.find_class`) is blocked, whichever library attempts it.
  - Network access (connect, bind, DNS, URL opening) is blocked.
  - Process creation is blocked, except in tier 3.
  - File creation, modification and deletion outside the declared output directories, the scratch directory and `result.json` are blocked.
  - Pickle, network and process attempts fail the run even if a library swallows the exception.
  - Plugin modules are imported *before* the guard is installed, so import-time system probes (e.g. NumPy's `lscpu`) don't count. No data is read before the guard is active.
- **Allowlists for everything named in config:**
  - torch layers and ops (ModelFactory), losses, optimizers, schedulers and architectures. Architecture parameters that would fetch or load weights (`weights`, `pretrained`, `checkpoint_path`, …) are refused; initial weights come only from an input via `init`.
  - Keras classes, modules and activations (no `Lambda`, no custom objects).
  - scikit-learn estimators and transformers (no `memory`, `n_jobs`, callbacks).
  - LLM `model_type`s.
  - Tier-2 ATen ops: no in-place variants, `out=` overloads, `empty*`, custom namespaces or higher-order ops.
  - Loss expressions are restricted to arithmetic over named components; no calls or attribute access.
- **SQL:** exactly one SELECT/set-operation query.
  - Every table must be a registered view or a CTE: no table functions (`read_csv`, `glob`, `query`, …), no file paths or URLs, no qualified names.
  - No DDL/DML, `COPY`, `ATTACH`, `INSTALL`, `LOAD`, `SET`, `PRAGMA` or parameters. Untyped functions must be on an allowlist.
  - Inputs are read by pyarrow and registered as Arrow views. DuckDB is connected with external access disabled, extension autoload/autoinstall and community/unsigned extensions disabled, Python replacement scans disabled, and the configuration locked, all *before* any view exists. A gap in the static policy therefore still cannot reach files or the network.
- **Hugging Face:** offline (`HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`), `local_files_only=True`, `trust_remote_code=False`, safetensors only, fast tokenizers only. Configs with `auto_map` are rejected.
- **Files:** globs are relative, with no `..` segments. Matches are resolved and must stay inside their dataset directory, and symlinks are refused.
- **Environment:** caches and `HOME` are redirected to a per-run scratch directory, which is removed at the end.

## Differential privacy

- **Budget:** `privacy.mode: dp` gives `(ε, δ)` for the whole recipe.
  - Every step that releases a trained artifact must be DP-capable.
  - A single trainer gets the whole budget. Several trainers must each declare `with.privacy`, and the shares must sum to at most the budget.
  - The ledger rejects any step whose accountant reports more than its share.
- **Accountants:**
  - Opacus DP-SGD (`torch/train`, `hf/sft`) uses `make_private_with_epsilon` with Poisson sampling and the **RDP** accountant. The ε reported is the accountant's ε *actually spent*.
  - `torch/graph-train` calibrates σ with Opacus and accounts every step itself.
  - dp-xgboost is pure ε-DP (δ spent = 0). The per-tree ε is solved so that `T · log(1 + q(e^{ε_tree} − 1)) = ε`, i.e. basic composition with Poisson-subsampling amplification at rate `q = subsample`.
- **Secret randomness:** DP noise and DP sampling use a seed from the OS CSPRNG, never the public recipe seed; a public seed would let anyone replay the noise. Non-DP runs are bit-for-bit reproducible, and DP runs are not, by design. Opacus `secure_mode` (torchcsprng) is not available, so the noise comes from torch's generator seeded from `secrets`.
- **Data-independent preprocessing:** under DP, `tabular/split` refuses anything fitted on private data:
  - `features`, category values, label classes and per-feature `bounds` must all be declared;
  - only constant fills are allowed (not mean/median);
  - only min-max scaling with declared bounds is allowed (not `standard`).

  dp-xgboost's histogram bins come from the same declared bounds.
- **Unit of privacy:** guarantees are per row. Under DP, `sql/duckdb` requires `privacy_unit`, a column that must be unique and non-null in the result, so each individual contributes at most one row.
- **δ bound:** DP-SGD requires `δ < 1/n_train`; otherwise the run fails rather than silently lowering δ, as v1 did.
- **Removed from v1:** the non-private "baseline" model that v1 trained on private data alongside the DP model.
- **Not covered by DP:** released evaluation metrics. Contracts bound them with `metrics_allowlist`.
- **dp-xgboost tip:** use strong `lambda` and `min_child_weight` (e.g. 50). Leaf values are noisy ratios G/(H+λ), and a small denominator lets the noise flip leaf signs. The credit-risk recipe went from unstable AUC (0.1–0.9 across runs) to 0.97 with this change.

## Tier 2: `depa-graph/v1`

`torch.export`'s own format is not used for untrusted artifacts. When a weights-only tensor load fails, `torch.export.load` retries with `weights_only=False` (arbitrary unpickling), and it rebuilds symbolic shapes with `sympy.sympify` (string evaluation).

A tier-2 artifact is a directory with two files:
- `graph.json`: core-ATen nodes with op names, literal arguments, `{ref}` links, dtype/device/layout encodings, parameter and buffer placeholders, and declared buffer mutations;
- `weights.safetensors`.

The loader parses JSON and safetensors only, validates the structure, resolves every call target through the op allowlist and rebuilds a `torch.fx` graph. The TDC produces the artifact on its own machine with `depa_train.tier2.export.export_graph(model, example_inputs, out_dir)`, which also returns allowlist violations. See `examples/tier2-mnist/`. Graphs that update buffers (BatchNorm) are refused under DP.

## Tier 3: `custom/entrypoint@1`

`command` is an absolute argv inside the image. The process sees:
- `DEPA_INPUTS_DIR`, `DEPA_OUTPUTS_DIR` (write `<name>/` per declared output), `DEPA_REPORT` and `DEPA_SEED`;
- a fixed `PATH` and the recipe's non-reserved `env`.

It may write `{"metrics": {...}, "privacy_spent": {"epsilon", "delta", "accountant"}}` to `DEPA_REPORT`. Undeclared metrics, an over-budget spend or a spend under a non-DP recipe fail the run. Outputs go through the same gate.

## Families, images and lock files

`pyproject.toml` has the extras `classic`, `torch` and `llm` (each includes `data`: duckdb, pandas, pyarrow, scikit-learn, h5py). `requirements/{classic,torch,llm}.txt` are fully pinned and hash-locked for Python 3.12 / linux x86_64, with torch from the PyTorch CPU index. Install with:

```
pip install --require-hashes -r requirements/<family>.txt && pip install --no-deps .
```

The classic family has extra constraints:
- **dp-xgboost 0.2.10** has no cp312 wheel, so it is built from the hashed sdist. The build needs g++, cmake and `CMAKE_POLICY_VERSION_MINIMUM=3.5` (cmake 4 removed old-policy compatibility).
- At run time it needs **NumPy < 2** and **setuptools < 81** (`pkg_resources`); keep setuptools in the trainer venv. The family therefore also pins **pyarrow < 26**, because pyarrow 26 requires NumPy 2.

## Framework choices

- **DuckDB + sqlglot instead of Spark:** in-process, no JVM, and it can be fully locked down. sqlglot provides a real AST for the policy instead of keyword blacklists.
- **transformers + PEFT for `hf/sft`, not Axolotl or torchtune:**
  - **Axolotl** is GPU-first. It pulls in a large dependency set (bitsandbytes, deepspeed/flash-attn options, datasets, hub tooling), which inflates the measured image, and its YAML exposes knobs like `trust_remote_code` and hub dataset loading that would all need policing.
  - **torchtune** builds every component from configuration via `_component_` dotted-path instantiation, i.e. arbitrary class construction from config. That is exactly what tier 1 forbids. It also supports a narrow set of model builders with its own checkpoint conversion, and is no longer actively developed.
  - A ~300-line harness-owned loop on transformers + PEFT keeps the image small and CPU-friendly, uses safetensors directly, and lets Opacus wrap the loop for DP.
  - **TRL** is not used. Its SFT trainer would add dependencies without anything a small CPU run needs, and DP needs our own loop anyway.
- **Opacus:** standard DP-SGD with an RDP accountant. Its accountant and noise calibration are reused for the functional tier-2 path.
- **skops** for scikit-learn: a non-pickle format with type auditing.
- **The dynamo ONNX exporter** (torch ≥ 2.9 default) with `external_data=False`, so each output is a single file.
- **Pydantic as the single source of truth:** typed configs for plugins, and the JSON Schemas are generated from them.

## Development

```
uv venv -p 3.12 .venv && uv pip install -r requirements/torch.txt pytest ruff && uv pip install --no-deps -e .
pytest -q          # tests skip cleanly when a family's frameworks are absent; run once per family venv
ruff check src tests && ruff format --check src tests
```

The test suite covers:
- schema and plugin `with` validation;
- every safety rule (pickle in inputs and outputs and under the guard, disallowed classes and ops, SQL attacks including `read_csv('/etc/passwd')`, `COPY`, `ATTACH`, `INSTALL` and URLs, with DuckDB lockdown tested independently);
- DP accounting;
- the tier-2 op allowlist;
- contract checks;
- a smoke run of every plugin.

Scenario recipes live in `../scenarios/<name>/recipe.yaml`. Each scenario's `prepare/prepare.sh --party <id> [--out DIR]` builds one provider's plaintext dataset.
