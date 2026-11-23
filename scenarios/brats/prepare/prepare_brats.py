"""BraTS TDP preparation: produce one PNG slice per modality and the segmentation mask per case.

By default the bundled, already-sliced sample (``../data/brats_<TDP>.tar.gz``, BraTS 2020 cases) is
unpacked. With ``--nifti-dir`` the provider instead converts its own BraTS NIfTI volumes: the middle
axial slice of each ``*.nii.gz`` is min-max normalised to 0-255 and written as PNG, keeping the
``BraTS20_Training_<id>/`` case layout.
"""

from __future__ import annotations

import argparse
import shutil
import tarfile
import tempfile
from pathlib import Path

import numpy as np

DATA = Path(__file__).resolve().parent.parent / "data"


def unpack_sample(tdp: str, out: Path) -> None:
    archive = DATA / f"brats_{tdp}.tar.gz"
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(archive) as tf:
        tf.extractall(tmp, filter="data")  # rejects absolute paths, '..', links outside, devices
        shutil.copytree(Path(tmp) / f"brats_{tdp}" / "preprocessed", out, dirs_exist_ok=True)


def convert_nifti(src: Path, out: Path) -> None:
    import nibabel as nib
    from PIL import Image

    for path in sorted(src.rglob("*.nii.gz")):
        volume = np.asarray(nib.load(str(path)).dataobj, dtype=np.float32)
        middle = volume[:, :, volume.shape[2] // 2]
        lo, hi = float(middle.min()), float(middle.max())
        scaled = ((middle - lo) / (hi - lo) * 255 if hi > lo else np.zeros_like(middle)).astype(np.uint8)
        target = out / path.parent.relative_to(src) / path.name.replace(".nii.gz", ".png")
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(scaled).save(target)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--tdp", required=True, choices=["A", "B", "C", "D"])
    parser.add_argument(
        "--nifti-dir", help="convert this provider's BraTS NIfTI volumes instead of the sample"
    )
    parser.add_argument("--out", help="dataset directory (default: ../data/brats_<TDP>/preprocessed)")
    args = parser.parse_args()
    out = Path(args.out) if args.out else DATA / f"brats_{args.tdp}" / "preprocessed"
    out.mkdir(parents=True, exist_ok=True)
    if args.nifti_dir:
        convert_nifti(Path(args.nifti_dir), out)
    else:
        unpack_sample(args.tdp, out)
    print(f"BraTS {args.tdp}: {sum(1 for _ in out.glob('*/*_seg.png'))} cases in {out}")


if __name__ == "__main__":
    main()
