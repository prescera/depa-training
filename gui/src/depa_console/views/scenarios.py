"""Scenarios: run the SDK's idempotent ``scenario deploy`` with live progress, cancellable.

With an environment file every configured platform is available. With a plain development
profile (``depa dev up``) the SDK derives an equivalent environment (file storage, the
``virtual`` release) and registers the scenario's parties first; only ``local`` runs then.
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from depa.deployment import DeployOptions, dc_setup, deploy_scenario, environment_from_profile
from depa.errors import DepaError, ValidationError
from fastapi import APIRouter, Request
from starlette.responses import Response

from depa_console.forms import checked, text
from depa_console.state import Console
from depa_console.tasks import TaskContext
from depa_console.web import Form, Me, console, flash, redirect, render, start_task

router = APIRouter()
PLATFORMS = {"local": "Local virtual clean room (development)", "aks": "Azure AKS (confidential ACI)",
             "gcp": "GCP Confidential Space"}  # fmt: skip


def scenarios(c: Console) -> list[dict[str, Any]]:
    root = c.settings.scenarios_root
    found = sorted(p.parent for p in root.glob("*/contract.yaml")) if root.is_dir() else []
    return [
        {
            "name": d.name,
            "path": d.resolve(),
            "datasets": len(list((d / "datasets").glob("*.yaml"))),
            "prepare": (d / "prepare").is_dir(),
        }
        for d in found
    ]


def configured_platforms(c: Console) -> dict[str, bool]:
    compute = (c.env.raw.get("compute") or {}) if c.env else {}
    return {"local": True, "aks": "aks" in compute, "gcp": "gcp" in compute}


@router.get("/scenarios")
def scenario_page(request: Request, me: Me) -> Response:
    c = console(request)
    return render(
        request,
        "scenarios.html",
        scenarios=scenarios(c),
        platforms=PLATFORMS,
        configured=configured_platforms(c),
        has_env=c.env is not None,
    )


@router.post("/scenarios/deploy")
def deploy(request: Request, me: Me, form: Form) -> Response:
    c = console(request)
    try:
        known = {s["name"]: s["path"] for s in scenarios(c)}
        name = text(form, "scenario", max_len=128)
        if name not in known:
            raise ValidationError("choose one of the listed scenarios", code="bad_input")
        platform = text(form, "platform", max_len=8)
        if not configured_platforms(c).get(platform):
            raise ValidationError("choose an available platform (aks, gcp need --env)", code="bad_input")
        prepare = text(form, "prepare", max_len=8)
        if prepare not in ("auto", "always", "never"):
            raise ValidationError("choose when to run the data preparation", code="bad_input")
        act_for = text(form, "act_for", max_len=8)
        if act_for not in ("me", "all"):
            raise ValidationError("choose which parties to act for", code="bad_input")
        opts = DeployOptions(
            platform=platform,
            parties=frozenset({me.party_id}) if act_for == "me" else None,
            storage_token=c.token("storage"),
            output_token=c.token("output"),
            prepare=prepare,
            run=checked(form, "run"),
            trainer_cmd=c.settings.dev_trainer_cmd if platform == "local" else None,
            work_dir=c.settings.work_dir / "scenarios" / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}",
        )
    except DepaError as exc:
        flash(request, "error", str(exc))
        return redirect("/scenarios")
    directory, home = Path(known[name]), c.settings.home

    def job(ctx: TaskContext) -> dict[str, Any]:
        env = c.env
        if env is None:
            ctx.log("development profile: deriving the environment and registering the scenario's parties")
            env = environment_from_profile(c.base, scenarios=[directory])
            dc_setup(env, env.profile(home), log=ctx.log)
        who = "every party whose keys are held" if opts.parties is None else me.party_id
        ctx.log(f"deploying {name} on {platform}, acting for {who}; work directory {opts.work_dir}")
        return deploy_scenario(directory, env, env.profile(home), replace(opts, cancel=ctx.cancel_event), log=ctx.log)

    return start_task(request, f"Deploy scenario {name} ({platform})", job, back="/scenarios")
