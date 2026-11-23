"""MNIST TDP preparation: download the IDX files, verify them and write the TDP's dataset.

Output: ``mnist-dataset.h5`` with ``features`` float32 [N, 1, 28, 28] (normalised with the MNIST
mean/std) and ``targets`` int64 [N]. IDX is a plain binary format (no pickle).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import struct
import urllib.request
from pathlib import Path

import h5py
import numpy as np

MIRROR = "https://ossci-datasets.s3.amazonaws.com/mnist/"
FILES = {
    "train-images-idx3-ubyte.gz": "f68b3c2dcbeaaa9fbdd348bbdeb94873",
    "train-labels-idx1-ubyte.gz": "d53e105ee54ea40749a09fcbcd1e9432",
}
DATA = Path(__file__).resolve().parent.parent / "data"


def fetch(raw: Path, name: str) -> bytes:
    path = raw / name
    if not path.exists():
        raw.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(MIRROR + name, path)  # noqa: S310 - fixed https URL
    blob = path.read_bytes()
    if hashlib.md5(blob).hexdigest() != FILES[name]:  # noqa: S324 - integrity check of a public file
        raise SystemExit(f"{name}: checksum mismatch")
    return gzip.decompress(blob)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--raw", default=str(DATA / "raw"))
    parser.add_argument("--out", default=str(DATA / "preprocessed"))
    parser.add_argument("--max-samples", type=int, help="keep only the first N training images (quick demos)")
    args = parser.parse_args()
    raw = Path(args.raw)
    images = fetch(raw, "train-images-idx3-ubyte.gz")
    labels = fetch(raw, "train-labels-idx1-ubyte.gz")
    magic, n, rows, cols = struct.unpack(">IIII", images[:16])
    if magic != 2051 or struct.unpack(">II", labels[:8]) != (2049, n):
        raise SystemExit("unexpected IDX headers")
    x = np.frombuffer(images[16:], dtype=np.uint8).reshape(n, 1, rows, cols)[: args.max_samples]
    y = np.frombuffer(labels[8:], dtype=np.uint8).astype(np.int64)[: args.max_samples]
    features = ((x.astype(np.float32) / 255.0) - 0.1307) / 0.3081
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with h5py.File(out / "mnist-dataset.h5", "w") as fh:
        fh.create_dataset("features", data=features)
        fh.create_dataset("targets", data=y)
    print(f"wrote {len(y)} MNIST images to {out}")


if __name__ == "__main__":
    main()
