from __future__ import annotations

import json
import math
import subprocess

from ...context import StepContext
from ...errors import PrivacyError, StepError
from . import CustomConfig

_SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
MAX_REPORT_BYTES = 1 << 20


def run(cfg: CustomConfig, ctx: StepContext) -> None:
    report = ctx.scratch / "report.json"
    work = ctx.scratch / "work"
    work.mkdir(exist_ok=True)
    for target in cfg.outputs:
        ctx.output_dir(target.output)
    env = {
        **cfg.env,
        "PATH": _SAFE_PATH,
        "HOME": str(work),
        "TMPDIR": str(work),
        "LANG": "C.UTF-8",
        "DEPA_INPUTS_DIR": str(ctx.input_root_dir),
        "DEPA_OUTPUTS_DIR": str(ctx.outputs_root_dir),
        "DEPA_REPORT": str(report),
        "DEPA_SEED": str(ctx.seed),
    }
    ctx.log("custom_started", argc=len(cfg.command))
    try:
        proc = subprocess.run(
            cfg.command,
            env=env,
            cwd=work,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=cfg.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise StepError(f"custom entrypoint exceeded {cfg.timeout_seconds}s") from None
    except OSError:
        raise StepError("custom entrypoint could not be started") from None
    if proc.returncode != 0:
        raise StepError(f"custom entrypoint exited with status {proc.returncode}")

    doc: dict = {}
    if report.is_file():
        if report.stat().st_size > MAX_REPORT_BYTES:
            raise StepError("custom report is too large")
        try:
            doc = json.loads(report.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise StepError("custom report is not valid JSON") from None
        if not isinstance(doc, dict) or set(doc) - {"metrics", "privacy_spent"}:
            raise StepError("custom report must be an object with 'metrics' and/or 'privacy_spent'")
    metrics = doc.get("metrics", {})
    if not isinstance(metrics, dict):
        raise StepError("custom report 'metrics' must be an object")
    for name, value in metrics.items():
        if name not in cfg.metrics:
            raise StepError(f"custom code reported undeclared metric '{name}'")
        if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value):
            raise StepError(f"custom metric '{name}' is not a finite number")
        ctx.metric(name, value)
    if ctx.dp:
        spent = doc.get("privacy_spent")
        if not isinstance(spent, dict):
            raise PrivacyError("tier-3 code under DP must report privacy_spent")
        eps, delta, accountant = spent.get("epsilon"), spent.get("delta"), spent.get("accountant")
        if not all(isinstance(x, int | float) for x in (eps, delta)) or not isinstance(accountant, str):
            raise PrivacyError("privacy_spent needs numeric epsilon/delta and an accountant name")
        ctx.spend(float(eps), float(delta), f"custom:{accountant[:64]}")
    elif "privacy_spent" in doc:
        raise PrivacyError("custom code reported privacy spend but the recipe is not DP")
