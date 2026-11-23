from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, TensorDataset

from ...context import StepContext
from ...data.readers import load_table, split_indices
from ...errors import SafetyError, StepError
from ...safety.files import reject_unsafe_files
from ...torchkit.train import OPACUS_ACCOUNTANT, check_delta, secret_generator
from . import MODEL_TYPES, PromptCompletion, SftConfig

IGNORE = -100


def _model_dir(ctx: StepContext, cfg: SftConfig) -> Path:
    root = ctx.input_root(cfg.base_model.input)
    directory = (root / cfg.base_model.path).resolve()
    if not directory.is_relative_to(root.resolve()) or not directory.is_dir():
        raise SafetyError("base_model.path must be a directory inside its input dataset")
    reject_unsafe_files(directory)
    for required in ("config.json", "tokenizer.json"):
        if not (directory / required).is_file():
            raise SafetyError(f"base model lacks {required}")
    if not any(directory.glob("*.safetensors")):
        raise SafetyError("base model weights must be safetensors")
    config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("model_type") not in MODEL_TYPES:
        raise SafetyError("base model architecture is not allowlisted")
    if "auto_map" in config or config.get("trust_remote_code"):
        raise SafetyError("base models that require remote code are not allowed")
    tok_config = directory / "tokenizer_config.json"
    if tok_config.is_file() and "auto_map" in json.loads(tok_config.read_text(encoding="utf-8")):
        raise SafetyError("tokenizers that require remote code are not allowed")
    return directory


def _load(directory: Path, max_parameters: int):
    from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer

    common = {"local_files_only": True, "trust_remote_code": False}
    model_config = AutoConfig.from_pretrained(directory, **common)
    model = AutoModelForCausalLM.from_pretrained(
        directory, config=model_config, use_safetensors=True, dtype=torch.float32, **common
    )
    if sum(p.numel() for p in model.parameters()) > max_parameters:
        raise StepError("base model exceeds max_parameters")
    tokenizer = AutoTokenizer.from_pretrained(directory, **common)
    if not tokenizer.is_fast:
        raise SafetyError("only fast (tokenizer.json) tokenizers are supported")
    return model, tokenizer


def _encode(cfg: SftConfig, tokenizer, rows: list[dict]) -> list[tuple[list[int], list[int]]]:
    eos = tokenizer.eos_token_id
    if eos is None:
        raise StepError("tokenizer has no EOS token")
    bos = (
        [tokenizer.bos_token_id]
        if getattr(tokenizer, "add_bos_token", False) and tokenizer.bos_token_id
        else []
    )
    examples = []
    for row in rows:
        if isinstance(cfg.format, PromptCompletion):
            prompt, completion = row.get(cfg.format.prompt_field), row.get(cfg.format.completion_field)
            if not isinstance(prompt, str) or not isinstance(completion, str):
                raise StepError("prompt/completion fields must be strings")
            p_ids = (
                bos
                + tokenizer(cfg.format.template.replace("{prompt}", prompt), add_special_tokens=False)[
                    "input_ids"
                ]
            )
            c_ids = tokenizer(completion, add_special_tokens=False)["input_ids"] + [eos]
        else:
            text = row.get(cfg.format.text_field)
            if not isinstance(text, str):
                raise StepError("text field must be a string")
            p_ids, c_ids = bos, tokenizer(text, add_special_tokens=False)["input_ids"] + [eos]
        ids = (p_ids + c_ids)[: cfg.max_seq_len]
        labels = ([IGNORE] * len(p_ids) if cfg.completion_only_loss else p_ids) + c_ids
        examples.append((ids, labels[: cfg.max_seq_len]))
    return examples


def _tensors(examples: list[tuple[list[int], list[int]]], pad_id: int) -> TensorDataset:
    length = max(len(ids) for ids, _ in examples)
    ids = torch.full((len(examples), length), pad_id, dtype=torch.long)
    labels = torch.full((len(examples), length), IGNORE, dtype=torch.long)
    mask = torch.zeros((len(examples), length), dtype=torch.long)
    for i, (x, y) in enumerate(examples):
        ids[i, : len(x)], labels[i, : len(y)], mask[i, : len(x)] = torch.tensor(x), torch.tensor(y), 1
    return TensorDataset(ids, mask, labels)


def _token_losses(model, ids, mask, labels) -> tuple[torch.Tensor, torch.Tensor]:
    logits = model(input_ids=ids, attention_mask=mask).logits[:, :-1]
    target = labels[:, 1:]
    nll = torch.nn.functional.cross_entropy(
        logits.reshape(-1, logits.shape[-1]), target.reshape(-1), ignore_index=IGNORE, reduction="none"
    ).view(target.shape)
    valid = (target != IGNORE).float()
    return nll * valid, valid


def _per_sample_loss(model, ids, mask, labels) -> torch.Tensor:
    nll, valid = _token_losses(model, ids, mask, labels)
    return (nll.sum(1) / valid.sum(1).clamp(min=1)).mean()


def run(cfg: SftConfig, ctx: StepContext) -> None:
    from peft import LoraConfig, get_peft_model, get_peft_model_state_dict
    from safetensors.torch import save_file
    from transformers import get_scheduler

    torch.manual_seed(ctx.seed)
    model, tokenizer = _load(_model_dir(ctx, cfg), cfg.max_parameters)
    pad_id = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
    names = {n.rsplit(".", 1)[-1] for n, _ in model.named_modules()}
    missing = [m for m in cfg.lora.target_modules if m not in names]
    if missing:
        raise StepError(f"LoRA target modules not found in the base model: {missing}")
    lora = LoraConfig(
        r=cfg.lora.r,
        lora_alpha=cfg.lora.alpha,
        lora_dropout=cfg.lora.dropout,
        target_modules=list(cfg.lora.target_modules),
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora)

    rows = load_table(ctx, cfg.data).to_pylist()
    if len(rows) < 2:
        raise StepError("need at least two training examples")
    fractions = {"train": 1 - cfg.eval_fraction, "eval": cfg.eval_fraction}
    parts = split_indices(len(rows), fractions, ctx.seed)
    examples = _encode(cfg, tokenizer, rows)
    train_ds = _tensors([examples[i] for i in parts["train"]], pad_id)
    eval_ds = _tensors([examples[i] for i in parts["eval"]], pad_id) if "eval" in parts else None
    trainable = [p for p in model.parameters() if p.requires_grad]
    ctx.log(
        "sft_data",
        train=len(train_ds),
        eval=len(eval_ds) if eval_ds else 0,
        trainable=sum(p.numel() for p in trainable),
    )

    t = cfg.training
    optimizer = torch.optim.AdamW(trainable, lr=t.learning_rate, weight_decay=t.weight_decay)
    engine = None
    if ctx.dp:
        from opacus import PrivacyEngine

        share = ctx.share
        assert share is not None
        check_delta(share.delta, len(train_ds))
        loader = DataLoader(train_ds, batch_size=t.batch_size, shuffle=True, generator=secret_generator(ctx))
        engine = PrivacyEngine(accountant="rdp")
        model, optimizer, loader = engine.make_private_with_epsilon(
            module=model,
            optimizer=optimizer,
            data_loader=loader,
            target_epsilon=share.epsilon,
            target_delta=share.delta,
            epochs=t.epochs,
            max_grad_norm=t.max_grad_norm,
            poisson_sampling=True,
            noise_generator=secret_generator(ctx),
        )
    else:
        loader = DataLoader(
            train_ds, batch_size=t.batch_size, shuffle=True, generator=torch.Generator().manual_seed(ctx.seed)
        )
    steps_per_epoch = max(1, math.ceil(len(loader) / t.gradient_accumulation))
    total = t.max_steps or steps_per_epoch * t.epochs
    scheduler = get_scheduler(
        t.scheduler,
        optimizer=optimizer,
        num_warmup_steps=int(t.warmup_ratio * total),
        num_training_steps=total,
    )

    step, losses = 0, []
    model.train()
    for epoch in range(t.epochs):
        for i, (ids, mask, labels) in enumerate(loader):
            loss = _per_sample_loss(model, ids, mask, labels)
            (loss / t.gradient_accumulation).backward()
            if len(ids):
                losses.append(float(loss.detach()))
            if (i + 1) % t.gradient_accumulation == 0 or i + 1 == len(loader):
                if engine is None:
                    torch.nn.utils.clip_grad_norm_(trainable, t.max_grad_norm)
                optimizer.step()
                scheduler.step()
                optimizer.zero_grad()
                step += 1
                if step >= total:
                    break
        ctx.log(
            "epoch_finished", epoch=epoch + 1, steps=step, train_loss=float(np.mean(losses[-len(loader) :]))
        )
        if step >= total:
            break
    ctx.metric("train_loss", float(np.mean(losses[-max(1, len(loader)) :])))
    if engine is not None:
        share = ctx.share
        assert share is not None
        ctx.spend(engine.get_epsilon(share.delta), share.delta, OPACUS_ACCOUNTANT)
        model = model.to_standard_module()

    if eval_ds is not None:
        model.eval()
        nll_sum, count = 0.0, 0.0
        with torch.no_grad():
            for ids, mask, labels in DataLoader(eval_ds, batch_size=t.batch_size):
                nll, valid = _token_losses(model, ids, mask, labels)
                nll_sum, count = nll_sum + float(nll.sum()), count + float(valid.sum())
        eval_loss = nll_sum / max(count, 1.0)
        ctx.metric("eval_loss", eval_loss)
        ctx.metric("perplexity", math.exp(min(eval_loss, 50.0)))

    out_dir = ctx.output_dir(cfg.save.output)
    state = {k: v.detach().contiguous() for k, v in get_peft_model_state_dict(model).items()}
    save_file(state, str(out_dir / "adapter_model.safetensors"))
    adapter_cfg = model.peft_config["default"].to_dict()
    adapter_cfg = json.loads(
        json.dumps(adapter_cfg, default=lambda o: sorted(o) if isinstance(o, set) else str(o))
    )
    adapter_cfg["base_model_name_or_path"] = None
    (out_dir / "adapter_config.json").write_text(json.dumps(adapter_cfg, indent=2, sort_keys=True))
