"""Plugin base class and registry.

A plugin is addressed as ``<name>@<major>``. Each plugin declares a semver version, the tier and
image families it is valid in, a pydantic model for its ``with`` block (from which its JSON Schema
is generated), static semantic checks, and a lazily imported implementation. Importing this module
or any plugin spec never imports a training framework, so validation stays cheap and side-effect
free.
"""

from __future__ import annotations

import importlib
import re
from typing import TYPE_CHECKING, Any, ClassVar

from .types import DATA_FAMILIES, OutputTarget, PrivacyShare, StepRef, Strict

if TYPE_CHECKING:
    from .context import StepContext
    from .recipe import Recipe

# Artifact kinds passed between steps by step id.
TABLE = "table"
TABULAR_SPLITS = "tabular_splits"
PREDICTIONS = "predictions"
NONE = "none"

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class ValidationContext:
    """What a plugin's static check may consult. Errors are collected, never raised."""

    def __init__(self, recipe: Recipe, step_kinds: dict[str, str], path: str):
        self.recipe = recipe
        self.aliases = {i.alias for i in recipe.inputs}
        self.outputs = {o.name: o.format for o in recipe.outputs}
        self.step_kinds = step_kinds
        self.path = path
        self.errors: list[str] = []

    @property
    def dp(self) -> bool:
        return self.recipe.privacy.mode == "dp"

    def error(self, field: str, message: str) -> None:
        self.errors.append(f"{self.path}.{field}: {message}" if field else f"{self.path}: {message}")

    def require_ref(self, ref: StepRef, kinds: set[str], field: str) -> None:
        kind = self.step_kinds.get(ref.step)
        if kind is None:
            self.error(field, f"refers to unknown or later step '{ref.step}'")
        elif kind not in kinds:
            self.error(field, f"step '{ref.step}' produces '{kind}', expected one of {sorted(kinds)}")

    def require_alias(self, alias: str, field: str) -> None:
        if alias not in self.aliases:
            self.error(field, f"input alias '{alias}' is not declared in recipe inputs")

    def require_output(self, target: OutputTarget, field: str, formats: set[str]) -> None:
        declared = self.outputs.get(target.output)
        if declared is None:
            self.error(field, f"output '{target.output}' is not declared in recipe outputs")
        elif declared != target.format:
            self.error(field, f"output '{target.output}' is declared with format '{declared}'")
        if target.format not in formats:
            self.error(
                field, f"format '{target.format}' is not supported here (supported: {sorted(formats)})"
            )


class Plugin:
    """Base class for plugins. Subclasses set the class variables and may override hooks."""

    name: ClassVar[str]
    version: ClassVar[str]
    tier: ClassVar[int] = 1
    families: ClassVar[frozenset[str]] = DATA_FAMILIES
    Config: ClassVar[type[Strict]]
    produces: ClassVar[str] = NONE
    summary: ClassVar[str]
    wraps: ClassVar[str]
    impl: ClassVar[str]  # "module:function", imported only when the step runs
    trains: ClassVar[bool] = False  # releases a trained artifact, so it consumes privacy budget
    dp_capable: ClassVar[bool] = False
    experimental: ClassVar[bool] = False
    preload_modules: ClassVar[tuple[str, ...]] = ()

    @property
    def major(self) -> int:
        return int(self.version.split(".", 1)[0])

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.major}"

    def json_schema(self) -> dict[str, Any]:
        schema = self.Config.model_json_schema(by_alias=True, mode="validation")
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["title"] = f"{self.ref} with"
        schema["x-depa-plugin"] = {
            "name": self.name,
            "version": self.version,
            "tier": self.tier,
            "families": sorted(self.families),
            "experimental": self.experimental,
        }
        return schema

    def check(self, cfg: Any, v: ValidationContext) -> None:
        """Static semantic validation beyond the JSON Schema. Must not read data."""

    def outputs(self, cfg: Any) -> list[OutputTarget]:
        save = getattr(cfg, "save", None)
        return [save] if isinstance(save, OutputTarget) else []

    def metrics(self, cfg: Any) -> list[str]:
        return []

    def privacy_share(self, cfg: Any) -> PrivacyShare | None:
        share = getattr(cfg, "privacy", None)
        return share if isinstance(share, PrivacyShare) else None

    def preload(self, cfg: Any) -> list[str]:
        """Modules imported before the runtime guard is installed.

        Some libraries probe the system at import time (e.g. NumPy 1.x runs ``lscpu``). Importing
        them before lockdown keeps those probes out of the guarded phase; no data is read yet.
        """
        return [self.impl.split(":")[0], *self.preload_modules]

    def run(self, cfg: Any, ctx: StepContext) -> Any:
        module_name, func_name = self.impl.split(":")
        func = getattr(importlib.import_module(module_name), func_name)
        return func(cfg, ctx)


class Registry:
    def __init__(self, plugins: list[Plugin]):
        self._by_ref: dict[str, Plugin] = {}
        for plugin in plugins:
            if not _SEMVER.match(plugin.version):
                raise ValueError(f"plugin {plugin.name} has invalid semver version {plugin.version!r}")
            if plugin.ref in self._by_ref:
                raise ValueError(f"duplicate plugin {plugin.ref}")
            self._by_ref[plugin.ref] = plugin

    def get(self, ref: str) -> Plugin | None:
        return self._by_ref.get(ref)

    def find(self, name: str) -> list[Plugin]:
        return [p for p in self._by_ref.values() if p.name == name]

    def all(self) -> list[Plugin]:
        return sorted(self._by_ref.values(), key=lambda p: p.ref)


_REGISTRY: Registry | None = None


def registry() -> Registry:
    global _REGISTRY  # noqa: PLW0603 - lazily built singleton
    if _REGISTRY is None:
        from .plugins import all_plugins

        _REGISTRY = Registry(all_plugins())
    return _REGISTRY
