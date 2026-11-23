"""Recipe executor: validate, isolate, run steps in order, gate outputs, write ``result.json``."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import random
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from .context import RunState, StepContext
from .errors import DepaError, RecipeError, StepError
from .formats import gate_outputs
from .log import status
from .privacy import PrivacyLedger
from .safety import guard
from .validation import CompiledRecipe, CompiledStep, require_valid

# Environment for every run: offline, no remote code, no unsafe torch.load, caches in scratch.
_OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
    "DO_NOT_TRACK": "1",
    "TORCH_FORCE_WEIGHTS_ONLY_LOAD": "1",
    "TOKENIZERS_PARALLELISM": "false",
    "PYTHONDONTWRITEBYTECODE": "1",
    "KMP_WARNINGS": "0",
}
_CACHE_VARS = (
    "HOME",
    "XDG_CACHE_HOME",
    "HF_HOME",
    "TORCH_HOME",
    "MPLCONFIGDIR",
    "KERAS_HOME",
    "TRITON_CACHE_DIR",
)


def step_seed(recipe_name: str, step_id: str, explicit: int | None) -> int:
    if explicit is not None:
        return explicit
    digest = hashlib.sha256(f"depa-train/{recipe_name}/{step_id}".encode()).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


def _seed_everything(seed: int) -> None:
    random.seed(seed)
    try:
        import numpy as np
    except ImportError:
        return
    np.random.seed(seed)


def _failed(error: str, ledger: PrivacyLedger | None) -> dict[str, Any]:
    privacy = (
        ledger.summary() if ledger else {"mode": "none", "epsilon": None, "delta": None, "accountant": "none"}
    )
    return {"status": "failed", "error": error, "metrics": {}, "privacy_spent": privacy, "outputs": []}


def _safe_message(exc: BaseException) -> str:
    """Our own exceptions carry safe messages; anything else may embed data, so report its type only."""
    if isinstance(exc, DepaError):
        return str(exc)
    return f"{type(exc).__name__} (details withheld: third-party error messages may contain data)"


def _prepare_environment(scratch: Path) -> dict[str, str | None]:
    """Point caches and temp files at the scratch directory. Returns the previous values."""
    previous = {k: os.environ.get(k) for k in (*_OFFLINE_ENV, *_CACHE_VARS, "TMPDIR")}
    previous["__tempdir__"] = tempfile.tempdir
    os.environ.update(_OFFLINE_ENV)
    for var in _CACHE_VARS:
        path = scratch / "home" if var == "HOME" else scratch / "cache" / var.lower()
        path.mkdir(parents=True, exist_ok=True)
        os.environ[var] = str(path)
    os.environ["TMPDIR"] = str(scratch / "tmp")
    (scratch / "tmp").mkdir(exist_ok=True)
    tempfile.tempdir = str(scratch / "tmp")
    return previous


def _restore_environment(previous: dict[str, str | None]) -> None:
    tempfile.tempdir = previous.pop("__tempdir__")
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _discard_outputs(outputs_dir: Path) -> None:
    """A failed run releases nothing: remove whatever steps wrote."""
    if outputs_dir.is_dir():
        for entry in outputs_dir.iterdir():
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)


def _check_dirs(compiled: CompiledRecipe, inputs_dir: Path, outputs_dir: Path) -> None:
    if not inputs_dir.is_dir():
        raise StepError("inputs directory does not exist")
    missing = [i.alias for i in compiled.recipe.inputs if not (inputs_dir / i.alias).is_dir()]
    if missing:
        raise StepError(f"missing input directories for aliases {missing}")
    outputs_dir.mkdir(parents=True, exist_ok=True)
    if any(outputs_dir.iterdir()):
        raise StepError("outputs directory must be empty")


def _run_step(state: RunState, step: CompiledStep) -> None:
    explicit = getattr(step.config, "seed", None)
    seed = step_seed(state.compiled.recipe.name, step.id, explicit)
    _seed_everything(seed)
    ctx = StepContext(state, step, seed)
    status("step_started", step=step.id, plugin=step.plugin.ref, version=step.plugin.version)
    started = time.monotonic()
    state.artifacts[step.id] = step.plugin.run(step.config, ctx)
    if step.share is not None and not state.ledger.spent(step.id):
        raise StepError(f"step '{step.id}' trained under DP but did not report its privacy spend")
    status("step_finished", step=step.id, seconds=time.monotonic() - started)


def execute(
    document: dict[str, Any],
    inputs_dir: str | Path,
    outputs_dir: str | Path,
    result_path: str | Path,
    *,
    install_guard: bool = True,
) -> dict[str, Any]:
    """Run a recipe end to end and write ``result.json``. Returns the result document.

    ``install_guard`` must stay True in production (the CLI always sets it). Tests that run
    several recipes in one interpreter disable it because the audit hook is irrevocable.
    """
    inputs_dir, outputs_dir, result_path = Path(inputs_dir), Path(outputs_dir), Path(result_path)
    ledger: PrivacyLedger | None = None
    scratch: Path | None = None
    previous_env: dict[str, str | None] | None = None
    owns_outputs = False  # only clean up an outputs directory we verified was empty
    try:
        compiled = require_valid(document)
    except RecipeError as exc:
        result = _failed(f"invalid recipe: {exc}", None)
        _write_result(result_path, result)
        return result

    started = time.monotonic()
    try:
        ledger = PrivacyLedger(compiled.recipe.privacy)
        _check_dirs(compiled, inputs_dir, outputs_dir)
        owns_outputs = True
        scratch = Path(tempfile.mkdtemp(prefix="depa-train-"))
        previous_env = _prepare_environment(scratch)
        for out in compiled.recipe.outputs:
            (outputs_dir / out.name).mkdir()
        for step in compiled.steps:
            for module in step.plugin.preload(step.config):
                try:
                    importlib.import_module(module)
                except ImportError:
                    raise StepError(
                        f"{step.plugin.ref} needs module '{module}', which is not installed in this image "
                        f"(family '{compiled.recipe.family}')"
                    ) from None
        if install_guard:
            guard.install(
                write_roots=[outputs_dir / o.name for o in compiled.recipe.outputs] + [scratch],
                write_files=[result_path],
                allow_subprocess=compiled.recipe.tier == 3,
            )
        state = RunState(compiled, inputs_dir.resolve(), outputs_dir.resolve(), scratch, ledger)
        status(
            "run_started", recipe=compiled.recipe.name, tier=compiled.recipe.tier, steps=len(compiled.steps)
        )
        for step in compiled.steps:
            try:
                _run_step(state, step)
            except Exception as exc:
                raise StepError(f"step '{step.id}' ({step.plugin.ref}) failed: {_safe_message(exc)}") from exc
        hard = guard.hard_violations()
        if hard:
            raise StepError(f"runtime guard violations: {hard}")
        outputs = gate_outputs(outputs_dir, compiled.recipe.outputs)
        missing = [m for m in compiled.recipe.metrics if m not in state.metrics]
        if missing:
            raise StepError(f"declared metrics were not produced: {missing}")
        ledger.check_total()
        result = {
            "status": "succeeded",
            "metrics": {m: state.metrics[m] for m in compiled.recipe.metrics},
            "privacy_spent": ledger.summary(),
            "outputs": outputs,
        }
        status("run_finished", seconds=time.monotonic() - started)
    except Exception as exc:
        status("run_failed", seconds=time.monotonic() - started)
        result = _failed(_safe_message(exc), ledger)
        if owns_outputs:
            _discard_outputs(outputs_dir)
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
        if previous_env is not None:
            _restore_environment(previous_env)
    _write_result(result_path, result)
    return result


def write_failure(result_path: Path, error: str) -> None:
    _write_result(result_path, _failed(error, None))


def _write_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
