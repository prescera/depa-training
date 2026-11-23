"""Privacy budget ledger. Composition across training steps is basic (sequential) composition."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..errors import PrivacyError
from ..recipe import PrivacySpec
from ..types import PrivacyShare

# Relative slack tolerated between a step's target budget and what its accountant reports
# (numerical inversion of noise multipliers is approximate, always on the conservative side).
_RTOL = 1e-6


@dataclass(frozen=True)
class Spend:
    step: str
    epsilon: float
    delta: float
    accountant: str


class PrivacyLedger:
    def __init__(self, privacy: PrivacySpec):
        self.privacy = privacy
        self.spends: list[Spend] = []

    def record(
        self, step: str, share: PrivacyShare | None, epsilon: float, delta: float, accountant: str
    ) -> None:
        if self.privacy.mode != "dp" or share is None:
            raise PrivacyError(f"step '{step}' reported privacy spend but has no DP budget")
        if not (math.isfinite(epsilon) and math.isfinite(delta)) or epsilon < 0 or delta < 0:
            raise PrivacyError(f"step '{step}' reported an invalid privacy spend")
        if epsilon > share.epsilon * (1 + _RTOL) or delta > share.delta * (1 + _RTOL):
            raise PrivacyError(
                f"step '{step}' spent epsilon={epsilon:.6g}, delta={delta:.3g} above its budget "
                f"epsilon={share.epsilon:.6g}, delta={share.delta:.3g}"
            )
        self.spends.append(Spend(step, float(epsilon), float(delta), accountant))

    def spent(self, step: str) -> bool:
        return any(s.step == step for s in self.spends)

    def summary(self) -> dict[str, object]:
        if self.privacy.mode == "none":
            return {"mode": "none", "epsilon": None, "delta": None, "accountant": "none"}
        if not self.spends:
            return {"mode": "dp", "epsilon": 0.0, "delta": 0.0, "accountant": "none"}
        epsilon = math.fsum(s.epsilon for s in self.spends)
        delta = math.fsum(s.delta for s in self.spends)
        if len(self.spends) == 1:
            accountant = self.spends[0].accountant
        else:
            accountant = "basic-composition(" + ",".join(s.accountant for s in self.spends) + ")"
        return {"mode": "dp", "epsilon": epsilon, "delta": delta, "accountant": accountant}

    def check_total(self) -> None:
        total = self.summary()
        if self.privacy.mode != "dp":
            return
        assert self.privacy.epsilon is not None
        assert self.privacy.delta is not None
        if total["epsilon"] > self.privacy.epsilon * (1 + _RTOL) or total["delta"] > self.privacy.delta * (
            1 + _RTOL
        ):
            raise PrivacyError("total privacy spend exceeds the recipe budget")
