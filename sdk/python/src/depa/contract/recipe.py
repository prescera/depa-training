"""Recipes (``depa-training/recipe/v1``): loading, digests and checks against a contract.

The trainer package (``depa_train``) owns the full recipe semantics; when importable (it is
in the CCR images) its ``depa_train.api.validate_recipe`` is consulted in addition to the
structural checks here, which cover everything the clean-room agent relies on (aliases become
directory names, output names select files, privacy and tier must match the contract).
"""

from __future__ import annotations

import importlib
import math
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from depa.contract.yamlio import load_yaml_text
from depa.core.encoding import digest, is_digest
from depa.core.ids import FAMILIES, check_dataset_id
from depa.errors import ValidationError, VerificationError

RECIPE_SCHEMA = "depa-training/recipe/v1"
ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
OUTPUT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
STEP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def load_recipe(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError(f"cannot read recipe {path}: {exc.strerror}", code="bad_recipe") from exc
    doc = load_yaml_text(text, source=str(path))
    if not isinstance(doc, dict):
        raise ValidationError(f"{path}: recipe must be a mapping", code="bad_recipe")
    return doc


def _num(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def structural_errors(recipe: Mapping[str, Any]) -> list[str]:
    errs: list[str] = []
    if recipe.get("schema") != RECIPE_SCHEMA:
        errs.append(f"recipe.schema must be {RECIPE_SCHEMA!r}")
    if not isinstance(recipe.get("name"), str) or not recipe.get("name"):
        errs.append("recipe.name is required")
    if recipe.get("tier") not in (1, 2, 3):
        errs.append("recipe.tier must be 1, 2 or 3")
    if recipe.get("family") not in FAMILIES:
        errs.append(f"recipe.family must be one of {', '.join(FAMILIES)}")
    inputs = recipe.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        errs.append("recipe.inputs must be a non-empty list")
    else:
        aliases: set[str] = set()
        for i, item in enumerate(inputs):
            if not isinstance(item, dict) or set(item) - {"alias", "dataset_id", "description"}:
                errs.append(f"recipe.inputs[{i}] must be {{alias, dataset_id}}")
                continue
            alias = item.get("alias")
            if not isinstance(alias, str) or not ALIAS_RE.fullmatch(alias):
                errs.append(f"recipe.inputs[{i}].alias must match {ALIAS_RE.pattern}")
            elif alias in aliases:
                errs.append(f"recipe.inputs[{i}].alias {alias!r} is duplicated")
            else:
                aliases.add(alias)
            try:
                check_dataset_id(item.get("dataset_id"))
            except ValidationError:
                errs.append(f"recipe.inputs[{i}].dataset_id is invalid")
    steps = recipe.get("steps")
    if not isinstance(steps, list) or not steps:
        errs.append("recipe.steps must be a non-empty list")
    else:
        ids: set[str] = set()
        for i, step in enumerate(steps):
            if not isinstance(step, dict) or not isinstance(step.get("uses"), str):
                errs.append(f"recipe.steps[{i}] needs a 'uses' string")
                continue
            sid = step.get("id")
            if not isinstance(sid, str) or not STEP_ID_RE.fullmatch(sid) or sid in ids:
                errs.append(f"recipe.steps[{i}].id missing, invalid or duplicated")
            else:
                ids.add(sid)
            if "with" in step and not isinstance(step["with"], dict):
                errs.append(f"recipe.steps[{i}].with must be a mapping")
    privacy = recipe.get("privacy")
    if not isinstance(privacy, dict) or privacy.get("mode") not in ("dp", "none"):
        errs.append("recipe.privacy.mode must be dp or none")
    elif privacy["mode"] == "dp" and not (
        _num(privacy.get("epsilon"))
        and privacy["epsilon"] > 0
        and _num(privacy.get("delta"))
        and 0 <= privacy["delta"] < 1
    ):
        errs.append("recipe.privacy needs epsilon > 0 and 0 <= delta < 1 for mode dp")
    outputs = recipe.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        errs.append("recipe.outputs must be a non-empty list")
    else:
        names: set[str] = set()
        for i, out in enumerate(outputs):
            name = out.get("name") if isinstance(out, dict) else None
            if not isinstance(name, str) or not OUTPUT_NAME_RE.fullmatch(name) or name in names:
                errs.append(f"recipe.outputs[{i}].name missing, invalid or duplicated")
            else:
                names.add(name)
            if not isinstance(out, dict) or not isinstance(out.get("format"), str):
                errs.append(f"recipe.outputs[{i}].format is required")
    metrics = recipe.get("metrics", [])
    if not isinstance(metrics, list) or not all(isinstance(m, str) for m in metrics):
        errs.append("recipe.metrics must be a list of names")
    return errs


def trainer_validator() -> Callable[[dict[str, Any]], list[str]] | None:
    """``depa_train.api.validate_recipe`` when the trainer package is installed."""
    try:
        api = importlib.import_module("depa_train.api")
    except ImportError:
        return None
    fn = getattr(api, "validate_recipe", None)
    return fn if callable(fn) else None


def trainer_errors(recipe: Mapping[str, Any]) -> list[str]:
    validator = trainer_validator()
    if validator is None:
        return []
    result = validator(dict(recipe))
    if not isinstance(result, list):
        return ["depa_train.api.validate_recipe returned a non-list"]
    return [str(e) for e in result]


def against_contract_errors(recipe: Mapping[str, Any], contract: Mapping[str, Any]) -> list[str]:
    """Checks the CCR agent repeats before running (spec §12 step 1)."""
    errs: list[str] = []
    computation = contract.get("computation", {})
    if recipe.get("tier") != computation.get("tier"):
        errs.append("recipe tier differs from the contract tier")
    contract_datasets = {d.get("dataset_id") for d in contract.get("datasets", [])}
    for item in recipe.get("inputs", []):
        if isinstance(item, dict) and item.get("dataset_id") not in contract_datasets:
            errs.append(f"recipe input {item.get('dataset_id')!r} is not a contract dataset")
    c_priv, r_priv = contract.get("privacy", {}), recipe.get("privacy", {})
    if c_priv.get("mode") == "dp":
        if not isinstance(r_priv, dict) or r_priv.get("mode") != "dp":
            errs.append("contract requires DP but the recipe privacy mode is not dp")
        else:
            if not (_num(r_priv.get("epsilon")) and r_priv["epsilon"] <= c_priv.get("epsilon", 0)):
                errs.append("recipe epsilon exceeds the contract epsilon")
            if not (_num(r_priv.get("delta")) and r_priv["delta"] <= c_priv.get("delta", 0)):
                errs.append("recipe delta exceeds the contract delta")
    allowed_formats = set(contract.get("output", {}).get("allowed_formats", []))
    for out in recipe.get("outputs", []):
        if isinstance(out, dict) and out.get("format") not in allowed_formats:
            errs.append(f"recipe output {out.get('name')!r} format {out.get('format')!r} is not allowed")
    return errs


def select_recipe(contract: Mapping[str, Any], recipe_digest: str) -> dict[str, Any]:
    """Return the embedded recipe whose digest matches, re-verifying ``digest(document)``."""
    if not is_digest(recipe_digest):
        raise VerificationError("recipe digest is malformed", code="bad_recipe_digest")
    for entry in contract.get("computation", {}).get("recipes", []):
        if entry.get("digest") == recipe_digest:
            document = entry.get("document")
            if not isinstance(document, dict) or digest(document) != recipe_digest:
                raise VerificationError("embedded recipe does not match its digest", code="recipe_digest_mismatch")
            return document
    raise VerificationError("recipe digest is not listed in the contract", code="recipe_not_in_contract")
