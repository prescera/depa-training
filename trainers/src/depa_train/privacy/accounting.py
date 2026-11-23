"""Closed-form accounting helpers (pure math, no framework imports)."""

from __future__ import annotations

import math


def amplified_epsilon(epsilon: float, sampling_rate: float) -> float:
    """Privacy amplification by Poisson subsampling for a pure epsilon-DP mechanism.

    A mechanism that is ``epsilon``-DP on its input, applied to a Poisson subsample with rate
    ``q``, is ``log(1 + q (e^epsilon - 1))``-DP (Balle, Barthe, Gaboardi 2018, add/remove
    neighbouring).
    """
    if not 0 < sampling_rate <= 1:
        raise ValueError("sampling_rate must be in (0, 1]")
    return math.log1p(sampling_rate * math.expm1(epsilon))


def per_round_epsilon(total_epsilon: float, rounds: int, sampling_rate: float) -> float:
    """Largest per-round epsilon such that ``rounds`` amplified rounds compose to ``total_epsilon``.

    Inverts ``rounds * amplified_epsilon(eps_round, q) = total_epsilon`` under basic composition.
    """
    if total_epsilon <= 0 or rounds < 1:
        raise ValueError("total_epsilon must be > 0 and rounds >= 1")
    target = total_epsilon / rounds
    return math.log1p(math.expm1(target) / sampling_rate)


def composed_epsilon(per_round: float, rounds: int, sampling_rate: float) -> float:
    """Basic composition of ``rounds`` subsampled pure-DP rounds."""
    return rounds * amplified_epsilon(per_round, sampling_rate)
