"""JSON Schema validation for protocol documents (schemas ship as package data)."""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator

from depa.errors import ValidationError

SCHEMAS = ("contract-v2", "release-policy-v1", "dataset-descriptor", "ccr-release")


@cache
def _validator(name: str) -> Draft202012Validator:
    if name not in SCHEMAS:
        raise ValueError(f"unknown schema {name}")
    text = resources.files("depa.contract").joinpath(f"schemas/{name}.schema.json").read_text("utf-8")
    schema = json.loads(text)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def schema_errors(doc: Any, name: str) -> list[str]:
    errors = sorted(_validator(name).iter_errors(doc), key=lambda e: list(map(str, e.absolute_path)))
    out = []
    for err in errors:
        where = "/".join(str(p) for p in err.absolute_path) or "<root>"
        out.append(f"{where}: {err.message[:300]}")
    return out


def require_schema(doc: Any, name: str, what: str) -> None:
    errors = schema_errors(doc, name)
    if errors:
        raise ValidationError(f"{what} is invalid:\n  " + "\n  ".join(errors), code="schema")
