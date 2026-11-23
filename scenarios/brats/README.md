# Brain tumour segmentation (BraTS)

| Task | Tier / family | Privacy | Parties | Data | Output |
|---|---|---|---|---|---|
| MRI tumour segmentation (UNet) over pooled imaging datasets | 1 / `torch` | DP-SGD, ε = 1.5, δ = 1e-4 | TDC + 4 TDPs | BraTS 2020 sample slices (PNG + masks) | safetensors model |

Four hospitals (`brats-a` … `brats-d`) each hold annotated brain MRI scans. These are sensitive
even when de-identified: combined quasi-identifiers can re-identify patients, and volumetric scans
allow facial reconstruction. A consumer wants a segmentation model trained on all four sets. Each
hospital keeps its scans in its own storage, and they are decrypted only inside an attested clean
room under a contract every hospital has signed.

## Parties and datasets

| Alias | Dataset id | Provider | Prepared by |
|---|---|---|---|
| `brats_a` … `brats_d` | `brats-a/brain-mri` … `brats-d/brain-mri` | Hospitals A–D | `prepare/prepare.sh --party brats-<x>` |

Preparation unpacks the bundled sample archives (`data/brats_<X>.tar.gz`). Set `NIFTI_DIR` to
convert a provider's own NIfTI volumes instead. Descriptors and policies are in
[`datasets/`](datasets), and the contract template is [`contract.yaml`](contract.yaml).

## Recipe

[`recipe.yaml`](recipe.yaml) runs two steps:
1. `torch/train@1` trains a UNet, declared as a ModelFactory configuration, on the pooled slices with DP-SGD;
2. `eval/segmentation@1` reports Dice, Jaccard, Hausdorff distance and test loss.

## Run it

```bash
depa dev up && depa dev scenario run brats

bash prepare/prepare_all.sh --inputs /tmp/brats/inputs
depa-train run --recipe recipe.yaml --inputs /tmp/brats/inputs --outputs /tmp/brats/out --result /tmp/brats/result.json
```

On Azure, run **Pilot · end-to-end demo** with scenario `brats`.

Verified result (CPU, 3 epochs, about 70 s): Dice 0.36–0.47, Jaccard 0.25–0.35, with ε 1.5 spent.
More epochs improve utility at the same privacy budget.

Data: BraTS 2020 ([Menze et al., 2015](https://doi.org/10.1109/TMI.2014.2377694);
[Bakas et al., 2017](https://doi.org/10.1038/sdata.2017.117)).
