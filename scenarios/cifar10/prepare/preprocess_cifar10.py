"""CIFAR-10 TDP preparation: download the official *binary* release and write the TDP's dataset.

The binary release (fixed-size records: 1 label byte + 3x32x32 pixel bytes) is parsed directly, so
no pickle is involved (the python release of CIFAR-10 is pickled). The archive's MD5 is verified.
Output: ``cifar10-dataset.safetensors`` with ``features`` float32 [N, 3, 32, 32] (normalised with
the CIFAR-10 channel mean/std) and ``targets`` int64 [N].
"""

from __future__ import annotations

import argparse
import hashlib
import tarfile
import urllib.request
from pathlib import Path

import numpy as np
from safetensors.numpy import save_file

URL = "https://www.cs.toronto.edu/~kriz/cifar-10-binary.tar.gz"
MD5 = "c32a1d4ab5d03f1284b67883e8d87530"
MEAN = np.array([0.4914, 0.4822, 0.4465], dtype=np.float32).reshape(1, 3, 1, 1)
STD = np.array([0.2470, 0.2435, 0.2616], dtype=np.float32).reshape(1, 3, 1, 1)
DATA = Path(__file__).resolve().parent.parent / "data"
RECORD = 1 + 3 * 32 * 32


def download(raw: Path) -> Path:
    archive = raw / "cifar-10-binary.tar.gz"
    if not archive.exists():
        raw.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(URL, archive)  # noqa: S310 - fixed https URL
    if hashlib.md5(archive.read_bytes()).hexdigest() != MD5:  # noqa: S324 - integrity check of a public file
        raise SystemExit("CIFAR-10 archive checksum mismatch")
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--raw", default=str(DATA / "raw"))
    parser.add_argument("--out", default=str(DATA / "preprocessed"))
    parser.add_argument("--max-samples", type=int, help="keep only the first N training images (quick demos)")
    args = parser.parse_args()
    records = []
    with tarfile.open(download(Path(args.raw))) as tf:
        for i in range(1, 6):
            member = tf.getmember(f"cifar-10-batches-bin/data_batch_{i}.bin")
            buf = tf.extractfile(member).read()
            records.append(np.frombuffer(buf, dtype=np.uint8).reshape(-1, RECORD))
    data = np.concatenate(records)[: args.max_samples]
    targets = data[:, 0].astype(np.int64)
    features = (data[:, 1:].reshape(-1, 3, 32, 32).astype(np.float32) / 255.0 - MEAN) / STD
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    save_file(
        {"features": np.ascontiguousarray(features), "targets": targets},
        str(out / "cifar10-dataset.safetensors"),
    )
    print(f"wrote {len(targets)} CIFAR-10 images to {out}")


if __name__ == "__main__":
    main()
