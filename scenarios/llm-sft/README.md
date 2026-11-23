# LLM supervised fine-tuning (LoRA, CPU)

| Task | Tier / family | Privacy | Parties | Data | Output |
|---|---|---|---|---|---|
| Note → JSON extraction (instruction tuning) | 1 / `llm` | none (DP variant possible, experimental) | TDC (base model) + 2 TDPs | synthetic JSONL | LoRA adapter (safetensors) |

Two data providers each hold a synthetic set of free-text notes paired with the JSON record they describe:
- **bank-a:** payment notes → `{amount, currency, merchant, date, method}`;
- **shop-b:** order notes → `{order_id, quantity, item, city, date}`.

The consumer (TDC) brings a small base model, [SmolLM2-135M](https://huggingface.co/HuggingFaceTB/SmolLM2-135M) (Apache-2.0, Llama architecture). The model is published as an input dataset like any other, so it reaches the clean room encrypted and is mounted read-only.

The recipe ([`recipe.yaml`](recipe.yaml)) has two steps:
1. pool both providers' rows with `sql/duckdb@1` (`UNION ALL`);
2. LoRA fine-tune the base model with `hf/sft@1` on CPU, using completion-only loss.

The base model is loaded offline from safetensors with `trust_remote_code=False`. Only the adapter (`adapter_model.safetensors` + `adapter_config.json`) leaves the clean room.

## Inputs

| Alias | Dataset id | Produced by | Default plaintext path |
|---|---|---|---|
| `base_model` | `tdc/smollm2-135m` | `prepare/prepare.sh --party tdc` (downloads `config.json`, `generation_config.json`, `model.safetensors`, `tokenizer.json`, `tokenizer_config.json`, `special_tokens_map.json`; set `BASE_MODEL_REVISION` to pin a commit) | `data/base_model/` |
| `bank_a` | `bank-a/transaction-notes` | `prepare/prepare.sh --party bank-a` (600 synthetic rows) | `data/bank_a/preprocessed/bank_a_notes.jsonl` |
| `shop_b` | `shop-b/order-notes` | `prepare/prepare.sh --party shop-b` (600 synthetic rows) | `data/shop_b/preprocessed/shop_b_notes.jsonl` |

Every script is non-interactive and accepts `--out DIR`. Each JSONL row is `{"example_id", "prompt", "completion"}`. A DP variant of the recipe sets `privacy: {mode: dp, ...}` and adds `privacy_unit: example_id` to the `pool` step.

## Run it

```bash
# Locally, end to end: CCF sandboxes plus a development-only virtual clean room
depa dev up && depa dev scenario run llm-sft

# Plaintext training only (no services, no encryption)
bash prepare/prepare_all.sh --inputs /tmp/llm/inputs        # needs network for the base model
depa-train run --recipe recipe.yaml --inputs /tmp/llm/inputs --outputs /tmp/llm/out --result /tmp/llm/result.json
```

Verified on CPU: about 2 minutes, eval loss 0.0025 (the synthetic task is highly regular).

On Azure, run **Pilot · end-to-end demo** with scenario `llm-sft`. The clean room uses the `llm`
image family.
