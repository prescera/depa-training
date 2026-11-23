# COVID-19 infection prediction

| Task | Tier / family | Privacy | Parties | Data | Output |
|---|---|---|---|---|---|
| Binary classification (MLP) over joined tabular records | 1 / `torch` | DP-SGD, ε = 1.0, δ = 1e-5 | TDC + 3 TDPs | Synthetic, de-identified CSV | ONNX model |

Three data providers each hold part of a patient's record: **ICMR** (COVID-19 test results),
**CoWIN** (vaccination records) and a **State War Room**, `index` (patient records). A consumer (TDC)
wants a model that predicts infection, which needs all three joined. None of the providers hands
data to the consumer or to each other. The join and training happen only inside an attested
clean room. The organisations and data are illustrative; the bundled data is synthetic.

## Parties and datasets

| Alias | Dataset id | Provider | Prepared by |
|---|---|---|---|
| `icmr` | `icmr/covid-test-results` | ICMR | `prepare/prepare.sh --party icmr` |
| `cowin` | `cowin/vaccination-records` | CoWIN | `prepare/prepare.sh --party cowin` |
| `index` | `index/patient-records` | State War Room | `prepare/prepare.sh --party index` |

Descriptors and release policies are in [`datasets/`](datasets), and the contract template is
[`contract.yaml`](contract.yaml).

## Recipe

[`recipe.yaml`](recipe.yaml) runs four steps:
1. `sql/duckdb@1` joins the three datasets on the hashed mobile number (a pseudonymous key);
2. `tabular/split@1` splits the data with declared, data-independent preprocessing (required under DP);
3. `torch/train@1` trains an MLP with DP-SGD (Opacus, RDP accountant);
4. `eval/classification@1` reports accuracy, F1, ROC AUC and test loss.

DP-SGD requires δ < 1/n, so δ is 1e-5. Only the ONNX model and the contract-allowlisted
metrics leave the clean room.

## Run it

```bash
# Locally, end to end: CCF sandboxes plus a development-only virtual clean room
depa dev up && depa dev scenario run covid

# Plaintext training only (no services, no encryption)
bash prepare/prepare_all.sh --inputs /tmp/covid/inputs
depa-train run --recipe recipe.yaml --inputs /tmp/covid/inputs --outputs /tmp/covid/out --result /tmp/covid/result.json
```

On Azure, run **Pilot · end-to-end demo** with scenario `covid` (see
[docs/guides/azure-pilot.md](../../docs/guides/azure-pilot.md)).

Verified result (CPU, about 10 s): accuracy 0.92, F1 0.86, ROC AUC 0.87, with ε 0.994 spent.
