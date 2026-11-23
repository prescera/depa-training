"""``depa dev``: local development stack and end-to-end scenario runs (virtual attestation)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from depa.cli.common import STATE, emit, handle_errors
from depa.dev import scenario as scenario_mod
from depa.dev import stack
from depa.profile import depa_home

app = typer.Typer(help="Local dev stack (CCF sandboxes) and scenario runs. Never for real data.", no_args_is_help=True)
scenario_app = typer.Typer(help="Run a scenario end to end on the dev stack.", no_args_is_help=True)
app.add_typer(scenario_app, name="scenario")


def _home() -> Path:
    return STATE.home or depa_home()


@app.command("up")
@handle_errors
def up(
    kms_port: Annotated[int, typer.Option(help="KMS sandbox port (18000-18199)")] = stack.DEFAULT_KMS_PORT,
    cs_port: Annotated[int, typer.Option(help="CS sandbox port (18000-18199)")] = stack.DEFAULT_CS_PORT,
    kms_repo: Annotated[Path | None, typer.Option(help="depa-training-kms-azure checkout")] = None,
    cs_repo: Annotated[Path | None, typer.Option(help="contract-service checkout")] = None,
) -> None:
    """Start both sandboxes, bootstrap governance, enable virtual attestation, pin peers, approve the dev release."""
    state = stack.up(_home(), kms_port=kms_port, cs_port=cs_port, kms_repo=kms_repo, cs_repo=cs_repo)
    emit(
        state,
        f"dev stack up: KMS {state['kms']['app_url']}  CS {state['cs']['app_url']}  (profile {_home()})",
    )


@app.command("down")
@handle_errors
def down(
    kms_repo: Annotated[Path | None, typer.Option()] = None,
    cs_repo: Annotated[Path | None, typer.Option()] = None,
    purge: Annotated[bool, typer.Option(help="Also delete the sandbox ledgers and certificates")] = False,
) -> None:
    """Stop the sandboxes started by `depa dev up`."""
    emit(stack.down(_home(), kms_repo=kms_repo, cs_repo=cs_repo, purge=purge))


@scenario_app.command("run")
@handle_errors
def run(
    name: Annotated[str, typer.Argument(help="Scenario name (scenarios/<name>) or a path")],
    scenarios_root: Annotated[Path, typer.Option(help="Directory containing the scenarios")] = Path("scenarios"),
    tdc: Annotated[str, typer.Option(help="TDC party id")] = "tdc",
    work_dir: Annotated[Path | None, typer.Option(help="Working directory (default: temp)")] = None,
    trainer_cmd: Annotated[str | None, typer.Option(help="Dev only: trainer command instead of depa-train")] = None,
) -> None:
    """TDPs publish, contract propose/verify/sign, local CCR run, receipt verification, model decrypt."""
    directory = Path(name) if Path(name).is_dir() else scenarios_root / name
    result = scenario_mod.run_scenario(
        directory,
        home=_home(),
        tdc=tdc,
        trainer_cmd=trainer_cmd,
        work_root=work_dir,
        log=lambda m: sys.stderr.write(f"[scenario] {m}\n"),
    )
    emit(result)
