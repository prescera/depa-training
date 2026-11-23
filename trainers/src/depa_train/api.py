"""Public Python API used by the clean-room agent and the SDK.

* :func:`validate_recipe` - schema and semantic validation of a recipe document.
* :func:`validate_against_contract` - consistency of a recipe with a ``depa-training/contract/v2``.
* :func:`run` - execute a recipe (same as ``depa-train run``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .validation import validate_recipe

__all__ = ["run", "validate_against_contract", "validate_recipe"]


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def validate_against_contract(recipe: dict[str, Any], contract: dict[str, Any]) -> list[str]:
    """Check that a recipe is consistent with a contract. Returns errors (empty = consistent).

    Checks: tier (and tier-3 custom CCR presence), privacy mode/epsilon/delta, output formats within
    ``output.allowed_formats`` and inputs within ``datasets``. Missing contract fields fail closed.
    """
    if not isinstance(recipe, dict):
        return ["recipe: must be an object"]
    if not isinstance(contract, dict):
        return ["contract: must be an object"]
    errors: list[str] = []
    computation = contract.get("computation")
    computation = computation if isinstance(computation, dict) else {}

    tier = computation.get("tier")
    if tier not in (1, 2, 3):
        errors.append("contract.computation.tier: missing or invalid")
    elif recipe.get("tier") != tier:
        errors.append(f"tier: recipe tier {recipe.get('tier')!r} does not match contract tier {tier}")
    custom_ccr = computation.get("custom_ccr")
    if tier == 3 and not isinstance(custom_ccr, dict):
        errors.append("contract.computation.custom_ccr: required for tier 3")
    if tier in (1, 2) and custom_ccr is not None:
        errors.append("contract.computation.custom_ccr: must be null for tier 1/2")

    errors.extend(_check_privacy(recipe.get("privacy"), contract.get("privacy")))

    output = contract.get("output")
    allowed_formats = output.get("allowed_formats") if isinstance(output, dict) else None
    if not isinstance(allowed_formats, list):
        errors.append("contract.output.allowed_formats: missing")
        allowed_formats = []
    for i, out in enumerate(recipe.get("outputs") or []):
        fmt = out.get("format") if isinstance(out, dict) else None
        if fmt not in allowed_formats:
            errors.append(f"outputs[{i}]: format {fmt!r} is not in contract output.allowed_formats")

    datasets = contract.get("datasets")
    if not isinstance(datasets, list):
        errors.append("contract.datasets: missing")
        datasets = []
    contract_ids = {d.get("dataset_id") for d in datasets if isinstance(d, dict)}
    for i, inp in enumerate(recipe.get("inputs") or []):
        dataset_id = inp.get("dataset_id") if isinstance(inp, dict) else None
        if dataset_id not in contract_ids:
            errors.append(f"inputs[{i}]: dataset {dataset_id!r} is not part of the contract")
    return errors


def _check_privacy(recipe_privacy: Any, contract_privacy: Any) -> list[str]:
    if not isinstance(contract_privacy, dict) or contract_privacy.get("mode") not in ("dp", "none"):
        return ["contract.privacy.mode: missing or invalid"]
    if not isinstance(recipe_privacy, dict) or recipe_privacy.get("mode") not in ("dp", "none"):
        return ["privacy.mode: missing or invalid"]
    if contract_privacy["mode"] == "none":
        return []  # a DP recipe is stricter than a non-DP contract and therefore consistent
    if recipe_privacy["mode"] != "dp":
        return ["privacy.mode: contract requires 'dp'"]
    errors = []
    for key in ("epsilon", "delta"):
        limit, value = _num(contract_privacy.get(key)), _num(recipe_privacy.get(key))
        if limit is None:
            errors.append(f"contract.privacy.{key}: missing")
        elif value is None:
            errors.append(f"privacy.{key}: missing")
        elif value > limit:
            errors.append(f"privacy.{key}: {value:g} exceeds contract {key} {limit:g}")
    return errors


def run(
    recipe: dict[str, Any], inputs: str | Path, outputs: str | Path, result: str | Path
) -> dict[str, Any]:
    """Execute a recipe with the runtime guard installed (irrevocable for this process)."""
    from .executor import execute

    return execute(recipe, inputs, outputs, result, install_guard=True)
