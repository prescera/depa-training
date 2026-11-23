"""``custom/entrypoint@1`` (tier 3): run a declared command inside a contract-approved custom image.

The command receives ``DEPA_INPUTS_DIR`` (one read-only directory per alias), ``DEPA_OUTPUTS_DIR``
(write ``<name>/`` for each declared output) and ``DEPA_REPORT`` (path where it may write
``{"metrics": {...}, "privacy_spent": {"epsilon", "delta", "accountant"}}``). It runs with a
scrubbed environment, no stdin and a deadline. The runtime then applies the same output gate,
metric declaration check and privacy-budget check as for tier 1/2.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints

from ...registry import NONE, Plugin, ValidationContext
from ...types import MetricName, OutputTarget, PrivacyShare, Strict

EnvKey = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")]
Arg = Annotated[str, StringConstraints(min_length=1, max_length=4096)]


class CustomConfig(Strict):
    command: list[Arg] = Field(
        min_length=1, max_length=256, description="argv; command[0] is an absolute path"
    )
    outputs: list[OutputTarget] = Field(default_factory=list, max_length=16)
    metrics: list[MetricName] = Field(default_factory=list, max_length=64)
    env: dict[EnvKey, Annotated[str, StringConstraints(max_length=4096)]] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=86_400, ge=1, le=7 * 86_400)
    privacy: PrivacyShare | None = None


class CustomEntrypoint(Plugin):
    name = "custom/entrypoint"
    version = "1.0.0"
    tier = 3
    families = frozenset({"custom"})
    Config = CustomConfig
    produces = NONE
    summary = "Tier 3: run an approved custom image's command under the same I/O and result contract"
    wraps = "subprocess (no shell, scrubbed environment, deadline)"
    impl = "depa_train.plugins.custom_entrypoint.impl:run"
    trains = True
    dp_capable = True  # the custom code reports its own spend; it is checked against the budget

    def check(self, cfg: CustomConfig, v: ValidationContext) -> None:
        if not cfg.command[0].startswith("/"):
            v.error("command[0]", "must be an absolute path inside the image")
        for i, target in enumerate(cfg.outputs):
            v.require_output(target, f"outputs[{i}]", {target.format})
        reserved = sorted(k for k in cfg.env if k.startswith("DEPA_") or k in {"PATH", "HOME", "LD_PRELOAD"})
        if reserved:
            v.error("env", f"reserved variables {reserved}")

    def outputs(self, cfg: CustomConfig) -> list[OutputTarget]:
        return list(cfg.outputs)

    def metrics(self, cfg: CustomConfig) -> list[str]:
        return list(cfg.metrics)
