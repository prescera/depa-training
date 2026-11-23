"""Recipe document model (``depa-training/recipe/v1``) and safe loading.

The pydantic models are the single source of truth; ``schemas/recipe.v1.json`` is generated from
them (``depa-train schema --write``) and a test keeps the committed file in sync.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import ConfigDict, Field, model_validator

from . import RECIPE_SCHEMA_ID
from .errors import RecipeError
from .types import Alias, DatasetId, Family, MetricName, OutputFormat, OutputName, PluginRef, StepId, Strict

MAX_RECIPE_BYTES = 1 << 20


_PRIVACY_JSON_SCHEMA_RULES = {
    "allOf": [
        {
            "if": {"properties": {"mode": {"const": "dp"}}},
            "then": {
                "required": ["epsilon", "delta"],
                "properties": {"epsilon": {"type": "number"}, "delta": {"type": "number"}},
            },
        },
        {
            "if": {"properties": {"mode": {"const": "none"}}},
            "then": {"not": {"anyOf": [{"required": ["epsilon"]}, {"required": ["delta"]}]}},
        },
    ]
}


class InputSpec(Strict):
    alias: Alias
    dataset_id: DatasetId


class StepSpec(Strict):
    id: StepId
    uses: PluginRef
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")

    @property
    def plugin_name(self) -> str:
        return self.uses.split("@", 1)[0]

    @property
    def plugin_major(self) -> int:
        return int(self.uses.split("@", 1)[1])


class PrivacySpec(Strict):
    model_config = ConfigDict(**Strict.model_config, json_schema_extra=_PRIVACY_JSON_SCHEMA_RULES)

    mode: Literal["dp", "none"]
    epsilon: float | None = Field(default=None, gt=0)
    delta: float | None = Field(default=None, ge=0, lt=1)

    @model_validator(mode="after")
    def _consistent(self) -> PrivacySpec:
        if self.mode == "dp" and (self.epsilon is None or self.delta is None):
            raise ValueError("privacy.mode 'dp' requires epsilon and delta")
        if self.mode == "none" and (self.epsilon is not None or self.delta is not None):
            raise ValueError("privacy.mode 'none' must not set epsilon or delta")
        return self


class OutputSpec(Strict):
    name: OutputName
    format: OutputFormat


class Recipe(Strict):
    schema_: Literal["depa-training/recipe/v1"] = Field(alias="schema")
    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$")
    tier: Literal[1, 2, 3]
    family: Family
    inputs: list[InputSpec] = Field(max_length=64)
    steps: list[StepSpec] = Field(min_length=1, max_length=64)
    privacy: PrivacySpec
    outputs: list[OutputSpec] = Field(max_length=16)
    metrics: list[MetricName] = Field(default_factory=list, max_length=64)


def recipe_json_schema() -> dict[str, Any]:
    schema = Recipe.model_json_schema(by_alias=True, mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"https://depa.world/schemas/{RECIPE_SCHEMA_ID}.json"
    schema["title"] = "DEPA Training recipe v1"
    return schema


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys instead of silently keeping the last one."""


def _construct_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def parse_recipe_text(text: str) -> dict[str, Any]:
    """Parse a recipe from JSON or YAML text (YAML is a superset of JSON). Safe constructors only."""
    if len(text.encode()) > MAX_RECIPE_BYTES:
        raise RecipeError([f"recipe exceeds {MAX_RECIPE_BYTES} bytes"])
    try:
        doc = yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 - SafeLoader subclass
    except yaml.YAMLError as exc:
        raise RecipeError([f"recipe is not valid YAML/JSON: {exc}"]) from None
    if not isinstance(doc, dict):
        raise RecipeError(["recipe must be a mapping"])
    # Round-trip through JSON so the document only contains JSON types (no YAML dates, sets, ...).
    try:
        return json.loads(json.dumps(doc, allow_nan=False))
    except (TypeError, ValueError):
        raise RecipeError(["recipe contains values that are not representable as JSON"]) from None


def load_recipe_file(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise RecipeError([f"recipe file not found: {p}"])
    if p.stat().st_size > MAX_RECIPE_BYTES:
        raise RecipeError([f"recipe exceeds {MAX_RECIPE_BYTES} bytes"])
    return parse_recipe_text(p.read_text(encoding="utf-8"))
