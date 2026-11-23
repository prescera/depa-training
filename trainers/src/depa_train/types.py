"""Constrained scalar types and small config building blocks shared by the recipe and plugins."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic import JsonValue as PydanticJsonValue

# Identifiers double as SQL view names and directory names, so they are deliberately narrow.
Alias = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
StepId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
OutputName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
MetricName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
ColumnName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_.\- ]{0,127}$")]
DatasetId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{1,62}/[a-z0-9][a-z0-9._-]{0,127}$")]
PluginRef = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]*/[a-z][a-z0-9-]*@[0-9]{1,4}$")]


# Relative path or glob inside an input directory: no absolute paths, no parent traversal, no
# backslashes. Checked again (resolved, containment-checked) at run time.
def _no_parent(value: str) -> str:
    if ".." in value.split("/"):
        raise ValueError("'..' path segments are not allowed")
    return value


RelGlob = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9_.\-*?\[\]][A-Za-z0-9_.\-*?\[\]/]{0,254}$"),
    AfterValidator(_no_parent),
]
DottedName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")]
Seed = Annotated[int, Field(ge=0, le=2**31 - 1)]

OUTPUT_FORMATS = ("safetensors", "onnx", "xgboost-json", "xgboost-ubj", "skops", "keras")
OutputFormat = Literal["safetensors", "onnx", "xgboost-json", "xgboost-ubj", "skops", "keras"]
FAMILIES = ("classic", "torch", "llm", "custom")
Family = Literal["classic", "torch", "llm", "custom"]
DATA_FAMILIES = frozenset({"classic", "torch", "llm"})

# JSON-safe parameter values passed to allowlisted constructors (pydantic.JsonValue: no objects).
JsonValue = PydanticJsonValue


class Strict(BaseModel):
    """Base for every config model: unknown keys are errors (fail closed)."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class StepRef(Strict):
    """Reference to the artifact produced by an earlier step."""

    step: StepId


class OutputTarget(Strict):
    """Where a step writes a model: a declared recipe output and its format."""

    output: OutputName
    format: OutputFormat


class Splits(Strict):
    """Train/validation/test fractions (must sum to 1)."""

    train: float = Field(gt=0, le=1)
    val: float = Field(default=0.0, ge=0, lt=1)
    test: float = Field(default=0.0, ge=0, lt=1)

    @model_validator(mode="after")
    def _sum_to_one(self) -> Splits:
        if abs(self.train + self.val + self.test - 1.0) > 1e-6:
            raise ValueError("splits must sum to 1")
        return self


class PrivacyShare(Strict):
    """Part of the recipe's DP budget assigned to one training step."""

    epsilon: float = Field(gt=0)
    delta: float = Field(ge=0, lt=1)
