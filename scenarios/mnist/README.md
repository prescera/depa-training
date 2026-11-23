# MNIST handwritten digits

| Task | Tier / family | Privacy | Parties | Data | Output |
|---|---|---|---|---|---|
| Multi-class image classification (CNN) | 1 / `torch` | none (non-personal data) | TDC + 1 TDP | MNIST | ONNX model |

A single provider and a small, fast CNN. It's the quickest scenario for checking that a deployment
works end to end: encryption, contract, attested clean room, encrypted model and receipts.

## Parties and datasets

| Alias | Dataset id | Prepared by |
|---|---|---|
| `mnist` | `mnist/handwritten-digits` | `prepare/prepare.sh --party mnist` |

Preparation downloads the MNIST IDX files (MD5-verified) and writes HDF5.

## Recipe

[`recipe.yaml`](recipe.yaml): `torch/train@1` trains a CNN built from configuration (replacing
v1's ONNX input model), then `eval/classification@1` reports accuracy, F1 and test loss. The model
leaves the clean room as ONNX.

## Run it

```bash
depa dev up && depa dev scenario run mnist

bash prepare/prepare_all.sh --inputs /tmp/mnist/inputs
depa-train run --recipe recipe.yaml --inputs /tmp/mnist/inputs --outputs /tmp/mnist/out --result /tmp/mnist/result.json
```

On Azure, run **Pilot · end-to-end demo** with scenario `mnist`.

Verified result (CPU, 1 epoch, about 10 s): accuracy 0.974, F1 0.974.

Data: [MNIST](https://yann.lecun.com/exdb/mnist/) (LeCun et al., 1998).
