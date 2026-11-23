# CIFAR-10 image classification

| Task | Tier / family | Privacy | Parties | Data | Output |
|---|---|---|---|---|---|
| Multi-class image classification (CNN) | 1 / `torch` | none (non-personal data) | TDC + 1 TDP | CIFAR-10 binary release | safetensors model |

A single provider shares a non-personal image dataset for training under a contract. It's the
simplest end-to-end path, useful for checking a deployment. Even without personal data, the
provider keeps control: its data is decrypted only inside an attested clean room, for the
contracted recipe, and every run leaves a verifiable receipt.

## Parties and datasets

| Alias | Dataset id | Prepared by |
|---|---|---|
| `cifar10` | `cifar10/object-recognition` | `prepare/prepare.sh --party cifar10` |

Preparation downloads the official CIFAR-10 *binary* release (MD5-verified) and writes
safetensors. The Python release is avoided because it requires unpickling.

## Recipe

[`recipe.yaml`](recipe.yaml): `torch/train@1` trains a CNN built from configuration, then
`eval/classification@1` reports accuracy, F1 and test loss.

## Run it

```bash
depa dev up && depa dev scenario run cifar10

bash prepare/prepare_all.sh --inputs /tmp/cifar10/inputs
depa-train run --recipe recipe.yaml --inputs /tmp/cifar10/inputs --outputs /tmp/cifar10/out --result /tmp/cifar10/result.json
```

On Azure, run **Pilot · end-to-end demo** with scenario `cifar10`.

Verified result (CPU, 2 epochs, about 10 s): accuracy 0.53, F1 0.53.

Data: [CIFAR-10](https://www.cs.toronto.edu/~kriz/cifar.html) (Krizhevsky, 2009).
