"""Fail-closed recipe validation. Runs entirely before any data is touched."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import jsonschema
from pydantic import ValidationError

from .errors import RecipeError
from .recipe import Recipe, recipe_json_schema
from .registry import Plugin, ValidationContext, registry
from .types import PrivacyShare


@dataclass(frozen=True)
class CompiledStep:
    index: int
    id: str
    plugin: Plugin
    config: Any
    share: PrivacyShare | None  # DP budget assigned to this step (None if it does not train under DP)


@dataclass(frozen=True)
class CompiledRecipe:
    recipe: Recipe
    document: dict[str, Any]
    steps: list[CompiledStep]


_RECIPE_VALIDATOR = jsonschema.Draft202012Validator(recipe_json_schema())


def _schema_errors(validator: jsonschema.Draft202012Validator, doc: Any, prefix: str) -> list[str]:
    errors = []
    for err in sorted(validator.iter_errors(doc), key=lambda e: [str(p) for p in e.absolute_path]):
        loc = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in err.absolute_path)
        errors.append(f"{prefix}{loc}: {err.message}")
    return errors


def _pydantic_errors(exc: ValidationError, prefix: str) -> list[str]:
    out = []
    for err in exc.errors(include_url=False, include_input=False):
        loc = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in err["loc"])
        out.append(f"{prefix}{loc}: {err['msg']}")
    return out


def compile_recipe(document: dict[str, Any]) -> tuple[CompiledRecipe | None, list[str]]:
    """Validate a recipe document and return its compiled form, or the list of errors."""
    if not isinstance(document, dict):
        return None, ["recipe: must be an object"]
    errors = _schema_errors(_RECIPE_VALIDATOR, document, "recipe")
    if errors:
        return None, errors
    try:
        recipe = Recipe.model_validate(document)
    except ValidationError as exc:
        return None, _pydantic_errors(exc, "recipe")

    if (recipe.tier == 3) != (recipe.family == "custom"):
        errors.append("recipe: tier 3 requires family 'custom' and family 'custom' requires tier 3")

    aliases = [i.alias for i in recipe.inputs]
    for dup in sorted({a for a in aliases if aliases.count(a) > 1}):
        errors.append(f"recipe.inputs: duplicate alias '{dup}'")
    names = [o.name for o in recipe.outputs]
    for dup in sorted({n for n in names if names.count(n) > 1}):
        errors.append(f"recipe.outputs: duplicate output name '{dup}'")
    for dup in sorted({m for m in recipe.metrics if recipe.metrics.count(m) > 1}):
        errors.append(f"recipe.metrics: duplicate metric '{dup}'")

    reg = registry()
    step_kinds: dict[str, str] = {}
    compiled: list[tuple[int, Any, Plugin, Any]] = []
    for i, step in enumerate(recipe.steps):
        path = f"recipe.steps[{i}]"
        if step.id in step_kinds:
            errors.append(f"{path}.id: duplicate step id '{step.id}'")
            continue
        plugin = reg.get(step.uses)
        if plugin is None:
            known = sorted(p.ref for p in reg.find(step.plugin_name))
            hint = f" (available: {', '.join(known)})" if known else ""
            errors.append(f"{path}.uses: unknown plugin '{step.uses}'{hint}")
            continue
        if plugin.tier == 3 and recipe.tier != 3:
            errors.append(f"{path}.uses: '{plugin.ref}' is only valid in tier-3 recipes")
        elif recipe.tier == 3 and plugin.tier != 3:
            errors.append(f"{path}.uses: tier-3 recipes may only use tier-3 plugins, not '{plugin.ref}'")
        elif plugin.tier > recipe.tier:
            errors.append(f"{path}.uses: '{plugin.ref}' is tier {plugin.tier}, recipe is tier {recipe.tier}")
        if recipe.family not in plugin.families:
            errors.append(
                f"{path}.uses: '{plugin.ref}' is not available in family '{recipe.family}' "
                f"(families: {', '.join(sorted(plugin.families))})"
            )
        with_errors = _schema_errors(
            jsonschema.Draft202012Validator(plugin.json_schema()), step.with_, f"{path}.with"
        )
        if with_errors:
            errors.extend(with_errors)
            step_kinds[step.id] = plugin.produces
            continue
        try:
            cfg = plugin.Config.model_validate(step.with_)
        except ValidationError as exc:
            errors.extend(_pydantic_errors(exc, f"{path}.with"))
            step_kinds[step.id] = plugin.produces
            continue
        vctx = ValidationContext(recipe, dict(step_kinds), f"{path}.with")
        plugin.check(cfg, vctx)
        errors.extend(vctx.errors)
        step_kinds[step.id] = plugin.produces
        compiled.append((i, step, plugin, cfg))

    errors.extend(_check_outputs(recipe, compiled))
    errors.extend(_check_metrics(recipe, compiled))
    shares, privacy_errors = _assign_privacy(recipe, compiled)
    errors.extend(privacy_errors)

    if errors:
        return None, errors
    steps = [
        CompiledStep(index=i, id=step.id, plugin=plugin, config=cfg, share=shares.get(step.id))
        for i, step, plugin, cfg in compiled
    ]
    return CompiledRecipe(recipe=recipe, document=document, steps=steps), []


def _check_outputs(recipe: Recipe, compiled: list[tuple[int, Any, Plugin, Any]]) -> list[str]:
    errors = []
    writers: dict[str, list[str]] = {}
    for _, step, plugin, cfg in compiled:
        for target in plugin.outputs(cfg):
            writers.setdefault(target.output, []).append(step.id)
    for out in recipe.outputs:
        steps = writers.get(out.name, [])
        if not steps:
            errors.append(f"recipe.outputs: output '{out.name}' is not written by any step")
        elif len(steps) > 1:
            errors.append(f"recipe.outputs: output '{out.name}' is written by several steps: {steps}")
    return errors


def _check_metrics(recipe: Recipe, compiled: list[tuple[int, Any, Plugin, Any]]) -> list[str]:
    producers: dict[str, list[str]] = {}
    for _, step, plugin, cfg in compiled:
        for metric in plugin.metrics(cfg):
            producers.setdefault(metric, []).append(step.id)
    errors = []
    for metric in recipe.metrics:
        steps = producers.get(metric, [])
        if not steps:
            errors.append(f"recipe.metrics: metric '{metric}' is not produced by any step")
        elif len(steps) > 1:
            errors.append(f"recipe.metrics: metric '{metric}' is produced by several steps: {steps}")
    return errors


def _assign_privacy(
    recipe: Recipe, compiled: list[tuple[int, Any, Plugin, Any]]
) -> tuple[dict[str, PrivacyShare], list[str]]:
    trainers = [(step, plugin, cfg) for _, step, plugin, cfg in compiled if plugin.trains]
    declared = {step.id: plugin.privacy_share(cfg) for step, plugin, cfg in trainers}
    errors: list[str] = []
    if recipe.privacy.mode == "none":
        for step_id, share in declared.items():
            if share is not None:
                errors.append(f"step '{step_id}': with.privacy is set but recipe privacy.mode is 'none'")
        return {}, errors

    eps, delta = recipe.privacy.epsilon, recipe.privacy.delta
    assert eps is not None  # guaranteed by PrivacySpec
    assert delta is not None
    for step, plugin, _ in trainers:
        if not plugin.dp_capable:
            errors.append(f"step '{step.id}': {plugin.ref} cannot train under privacy.mode 'dp'")
    if not trainers:
        errors.append("recipe.privacy: mode 'dp' but no step trains a model")
        return {}, errors
    if len(trainers) == 1 and declared[trainers[0][0].id] is None:
        return {trainers[0][0].id: PrivacyShare(epsilon=eps, delta=delta)}, errors
    missing = [sid for sid, share in declared.items() if share is None]
    if missing:
        errors.append(
            f"recipe.privacy: several training steps share the DP budget; set with.privacy on {missing}"
        )
        return {}, errors
    shares = {sid: share for sid, share in declared.items() if share is not None}
    total_eps = math.fsum(s.epsilon for s in shares.values())
    total_delta = math.fsum(s.delta for s in shares.values())
    if total_eps > eps * (1 + 1e-12):
        errors.append(f"recipe.privacy: step epsilons sum to {total_eps:g} > budget {eps:g}")
    if total_delta > delta * (1 + 1e-12):
        errors.append(f"recipe.privacy: step deltas sum to {total_delta:g} > budget {delta:g}")
    return shares, errors


def validate_recipe(document: dict[str, Any]) -> list[str]:
    """Return the list of validation errors (empty if the recipe is valid)."""
    return compile_recipe(document)[1]


def require_valid(document: dict[str, Any]) -> CompiledRecipe:
    compiled, errors = compile_recipe(document)
    if compiled is None:
        raise RecipeError(errors)
    return compiled
