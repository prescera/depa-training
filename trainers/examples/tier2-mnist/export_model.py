"""TDC side (tier 2): define a model in PyTorch and export it as a depa-graph/v1 artifact.

The CCR never runs this code: it receives only graph.json (allowlisted ATen ops) and
weights.safetensors, published as an input dataset (here ``tdc/mnist-cnn-graph``).
    python export_model.py --out <dir>
"""

from __future__ import annotations

import argparse

import torch
from torch import nn

from depa_train.tier2.export import export_graph


class Net(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(1, 16, 3)
        self.norm1 = nn.GroupNorm(4, 16)  # GroupNorm, not BatchNorm: per-sample gradients stay independent
        self.conv2 = nn.Conv2d(16, 32, 3)
        self.fc1 = nn.Linear(32 * 5 * 5, 64)
        self.fc2 = nn.Linear(64, 10)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = nn.functional.max_pool2d(torch.relu(self.norm1(self.conv1(x))), 2)
        x = nn.functional.max_pool2d(torch.relu(self.conv2(x)), 2)
        return self.fc2(torch.relu(self.fc1(torch.flatten(x, 1))))


def main() -> None:
    fmt = argparse.RawDescriptionHelpFormatter
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=fmt)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    torch.manual_seed(0)
    errors = export_graph(Net().train(), (torch.randn(2, 1, 28, 28),), args.out)
    if errors:
        raise SystemExit("graph uses ops outside the tier-2 allowlist:\n" + "\n".join(errors))
    print(f"exported depa-graph/v1 to {args.out}")


if __name__ == "__main__":
    main()
