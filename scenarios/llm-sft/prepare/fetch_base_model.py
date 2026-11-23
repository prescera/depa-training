"""TDC side: download the base model that the recipe fine-tunes and lay it out as an input dataset.

Default: HuggingFaceTB/SmolLM2-135M (Apache-2.0, ~270 MB, Llama architecture). Only safetensors
weights and JSON config/tokenizer files are fetched; no code, no pickles. The resulting directory is
encrypted and published like any other dataset (``tdc/smollm2-135m``) and mounted read-only in the CCR.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

FILES = [
    "config.json",
    "generation_config.json",
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
]
DATA = Path(__file__).resolve().parent.parent / "data"


def main() -> None:
    from huggingface_hub import hf_hub_download

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repo", default="HuggingFaceTB/SmolLM2-135M")
    parser.add_argument("--revision", default="main", help="pin a commit hash for reproducible builds")
    parser.add_argument("--out", default=str(DATA / "base_model"))
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    digests = {}
    for name in FILES:
        path = Path(hf_hub_download(args.repo, name, revision=args.revision, local_dir=out))
        digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    cache = out / ".cache"
    if cache.is_dir():  # huggingface_hub bookkeeping; not part of the dataset
        for p in sorted(cache.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
        cache.rmdir()
    print(json.dumps({"repo": args.repo, "revision": args.revision, "sha256": digests}, indent=2))


if __name__ == "__main__":
    main()
