"""``hf/sft@1``: LoRA supervised fine-tuning of a small causal LM on CPU (transformers + PEFT).

The base model is an input dataset (safetensors + config/tokenizer JSON). It is loaded offline,
with ``trust_remote_code=False``, safetensors only, and only for allowlisted architectures. The
training loop is harness-owned (per-sample completion-only loss) so that Opacus DP-SGD can wrap it
(experimental). Output: the LoRA adapter as ``adapter_model.safetensors`` + ``adapter_config.json``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from ...data import TableSource
from ...registry import NONE, TABLE, Plugin, ValidationContext
from ...types import Alias, ColumnName, OutputTarget, PrivacyShare, RelGlob, Seed, StepRef, Strict

MODEL_TYPES = frozenset(
    {
        "llama",
        "mistral",
        "qwen2",
        "qwen3",
        "gpt2",
        "gpt_neox",
        "phi",
        "phi3",
        "gemma",
        "gemma2",
        "olmo",
        "olmo2",
        "stablelm",
        "opt",
        "granite",
        "smollm3",
    }
)
ModuleName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")]


class BaseModelRef(Strict):
    input: Alias
    path: RelGlob = "."


class PromptCompletion(Strict):
    kind: Literal["prompt_completion"]
    prompt_field: ColumnName = "prompt"
    completion_field: ColumnName = "completion"
    template: str = Field(default="{prompt}", max_length=2048, description="must contain '{prompt}' once")

    @model_validator(mode="after")
    def _template(self) -> PromptCompletion:
        if self.template.count("{prompt}") != 1:
            raise ValueError("template must contain '{prompt}' exactly once")
        return self


class PlainText(Strict):
    kind: Literal["text"]
    text_field: ColumnName = "text"


class LoraSpec(Strict):
    r: int = Field(default=8, ge=1, le=256)
    alpha: float = Field(default=16, gt=0)
    dropout: float = Field(default=0.0, ge=0, lt=1)
    target_modules: list[ModuleName] = Field(min_length=1, max_length=32)


class SftTraining(Strict):
    epochs: int = Field(default=1, ge=1, le=100)
    max_steps: int | None = Field(default=None, ge=1)
    batch_size: int = Field(default=8, ge=1, le=1024)
    gradient_accumulation: int = Field(default=1, ge=1, le=256)
    learning_rate: float = Field(default=2e-4, gt=0, le=1)
    weight_decay: float = Field(default=0.0, ge=0)
    warmup_ratio: float = Field(default=0.0, ge=0, lt=1)
    scheduler: Literal["linear", "cosine", "constant"] = "linear"
    max_grad_norm: float = Field(default=1.0, gt=0, description="gradient clipping (per-sample under DP)")


class SftConfig(Strict):
    base_model: BaseModelRef
    data: TableSource
    format: Annotated[PromptCompletion | PlainText, Field(discriminator="kind")]
    max_seq_len: int = Field(default=512, ge=16, le=8192)
    completion_only_loss: bool = True
    lora: LoraSpec
    training: SftTraining = Field(default_factory=SftTraining)
    eval_fraction: float = Field(default=0.1, ge=0, le=0.5)
    max_parameters: int = Field(default=3_000_000_000, ge=1)
    privacy: PrivacyShare | None = None
    save: OutputTarget
    seed: Seed | None = None


class HfSft(Plugin):
    name = "hf/sft"
    version = "1.0.0"
    families = frozenset({"llm"})
    Config = SftConfig
    produces = NONE
    summary = "LoRA SFT of a small causal LM on CPU; adapter as safetensors; optional DP (experimental)"
    wraps = "transformers + peft (+ Opacus)"
    impl = "depa_train.plugins.hf_sft.impl:run"
    preload_modules = (
        "transformers",
        "peft",
        "opacus",
        "safetensors.torch",
        "pyarrow.json",
        "pyarrow.parquet",
        "pyarrow.csv",
    )
    trains = True
    dp_capable = True

    def check(self, cfg: SftConfig, v: ValidationContext) -> None:
        v.require_alias(cfg.base_model.input, "base_model.input")
        if isinstance(cfg.data, StepRef):
            v.require_ref(cfg.data, {TABLE}, "data")
        else:
            v.require_alias(cfg.data.input, "data.input")
        v.require_output(cfg.save, "save", {"safetensors"})
        if v.dp:
            if cfg.training.gradient_accumulation != 1:
                v.error("training.gradient_accumulation", "must be 1 under DP (Poisson-sampled batches)")
            if cfg.training.max_steps is not None:
                v.error("training.max_steps", "use epochs under DP (the accountant calibrates to epochs)")
            if cfg.privacy is not None and cfg.privacy.delta <= 0:
                v.error("privacy.delta", "DP-SGD needs delta > 0")

    def metrics(self, cfg: SftConfig) -> list[str]:
        return ["train_loss", "eval_loss", "perplexity"]
