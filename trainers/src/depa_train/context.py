"""Per-step execution context: the only way plugins reach inputs, outputs, artifacts and budgets."""

from __future__ import annotations

import math
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import StepError
from .log import status
from .privacy import PrivacyLedger
from .safety.files import resolve_files
from .types import PrivacyShare, StepRef
from .validation import CompiledRecipe, CompiledStep


@dataclass
class RunState:
    compiled: CompiledRecipe
    inputs_dir: Path
    outputs_dir: Path
    scratch_dir: Path
    ledger: PrivacyLedger
    artifacts: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)


class StepContext:
    def __init__(self, state: RunState, step: CompiledStep, seed: int):
        self._state = state
        self.step = step
        self.seed = seed
        self._allowed_outputs = {t.output: t.format for t in step.plugin.outputs(step.config)}
        self.scratch = state.scratch_dir / step.id
        self.scratch.mkdir(parents=True, exist_ok=True)

    @property
    def step_id(self) -> str:
        return self.step.id

    @property
    def dp(self) -> bool:
        return self.step.share is not None

    @property
    def share(self) -> PrivacyShare | None:
        return self.step.share

    @staticmethod
    def secure_seed() -> int:
        """Seed for DP noise and sampling. Never derived from the (public) recipe."""
        return secrets.randbits(63)

    @property
    def input_root_dir(self) -> Path:
        """The whole inputs directory (tier 3 only: the custom image reads it directly)."""
        return self._state.inputs_dir

    @property
    def outputs_root_dir(self) -> Path:
        """The whole outputs directory (tier 3 only; the output gate checks it afterwards)."""
        return self._state.outputs_dir

    def input_root(self, alias: str) -> Path:
        if alias not in {i.alias for i in self._state.compiled.recipe.inputs}:
            raise StepError(f"input alias '{alias}' is not declared")
        root = self._state.inputs_dir / alias
        if not root.is_dir():
            raise StepError(f"input directory for alias '{alias}' is missing")
        return root

    def files(self, alias: str, pattern: str, *, suffixes: frozenset[str] | None = None) -> list[Path]:
        paths = resolve_files(self.input_root(alias), pattern, suffixes=suffixes)
        if not paths:
            raise StepError(f"no files match '{pattern}' in input '{alias}'")
        return paths

    def output_dir(self, name: str) -> Path:
        if name not in self._allowed_outputs:
            raise StepError(f"step '{self.step_id}' may not write output '{name}'")
        path = self._state.outputs_dir / name
        path.mkdir(exist_ok=True)
        return path

    def artifact(self, ref: StepRef) -> Any:
        if ref.step not in self._state.artifacts:
            raise StepError(f"artifact of step '{ref.step}' is not available")
        return self._state.artifacts[ref.step]

    def metric(self, name: str, value: float) -> None:
        value = float(value)
        if not math.isfinite(value):
            raise StepError(f"metric '{name}' is not a finite number")
        self._state.metrics[name] = value

    def spend(self, epsilon: float, delta: float, accountant: str) -> None:
        self._state.ledger.record(self.step_id, self.share, epsilon, delta, accountant)
        status("privacy_spent", step=self.step_id, epsilon=epsilon, delta=delta, accountant=accountant)

    def log(self, event: str, **fields: object) -> None:
        status(event, step=self.step_id, **fields)
