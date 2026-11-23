"""Template loading: ``${NAME}`` / ``${NAME:-default}`` substitution, then strict YAML parsing.

Substitution is textual and happens before parsing, so a default may be a YAML flow
collection (``${DEPA_PLATFORMS:-[aci-snp, gcp-cs-tdx]}``). The YAML loader is ``SafeLoader``
without the implicit timestamp resolver (timestamps stay strings, as JCS requires) and with
duplicate keys rejected.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from depa.core.encoding import loads_object
from depa.errors import ValidationError

_VAR_RE = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader minus timestamps, rejecting duplicate mapping keys."""


_StrictLoader.yaml_implicit_resolvers = {
    key: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _construct_mapping(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    out: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in out:
            raise ValidationError(f"duplicate YAML key {key!r} (line {key_node.start_mark.line + 1})", code="bad_yaml")
        out[key] = loader.construct_object(value_node, deep=deep)
    return out


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def substitute(text: str, env: Mapping[str, str]) -> str:
    missing: list[str] = []

    def repl(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        value = env.get(name)
        if value is not None and value != "":
            return value
        if default is not None:
            return default
        missing.append(name)
        return ""

    result = _VAR_RE.sub(repl, text)
    if missing:
        raise ValidationError(f"template variables not set: {', '.join(sorted(set(missing)))}", code="unset_vars")
    return result


def load_yaml_text(text: str, *, source: str = "<string>") -> Any:
    try:
        return yaml.load(text, Loader=_StrictLoader)  # noqa: S506 - _StrictLoader derives from SafeLoader
    except yaml.YAMLError as exc:
        raise ValidationError(f"{source}: invalid YAML: {exc}", code="bad_yaml") from exc


def load_template(path: Path, env: Mapping[str, str]) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError(f"cannot read {path}: {exc.strerror}", code="bad_template") from exc
    doc = load_yaml_text(substitute(text, env), source=str(path))
    if not isinstance(doc, dict):
        raise ValidationError(f"{path}: expected a mapping at the top level", code="bad_template")
    return doc


def load_document(path: Path, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Load a document: ``.json`` strictly (no substitution), anything else as a YAML template."""
    if path.suffix == ".json":
        try:
            return loads_object(path.read_bytes())
        except OSError as exc:
            raise ValidationError(f"cannot read {path}: {exc.strerror}", code="bad_template") from exc
    return load_template(path, env or {})
