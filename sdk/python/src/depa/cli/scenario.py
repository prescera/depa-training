"""``depa scenario deploy``: drive a scenario end to end from an environment file."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from depa.cli.common import STATE, emit, fail, handle_errors, read_env_secret
from depa.deployment import (
    DeployOptions,
    TeardownOptions,
    check_scenario,
    dc_setup,
    deploy_scenario,
    environment_from_profile,
    parse_stages,
    teardown_scenario,
)
from depa.env import load_env
from depa.profile import depa_home

app = typer.Typer(help="Deploy and run scenarios end to end (pilots / demos).", no_args_is_help=True)


@app.command("deploy")
@handle_errors
def deploy(
    name: Annotated[str, typer.Argument(help="Scenario name (scenarios/<name>) or a path")],
    env_file: Annotated[
        Path | None, typer.Option("--env", help="depa-env.yaml (or the global --env); omit for --platform local dev")
    ] = None,
    platform: Annotated[str, typer.Option(help="aks | gcp | local")] = "aks",
    parties: Annotated[str, typer.Option(help="'all' or comma-separated party ids to act for")] = "all",
    scenarios_root: Annotated[Path, typer.Option(help="Directory containing the scenarios")] = Path("scenarios"),
    storage_token_env: Annotated[
        str | None, typer.Option(help="Env var with a bearer token for TDP storage (gcs/azure)")
    ] = None,
    output_token_env: Annotated[
        str | None, typer.Option(help="Env var with a bearer token for the TDC output storage")
    ] = None,
    prepare: Annotated[str, typer.Option(help="auto | always | never: run scenarios/<name>/prepare")] = "auto",
    run: Annotated[bool, typer.Option("--run/--no-run", help="Submit a job once the contract is active")] = True,
    wait_timeout: Annotated[int | None, typer.Option(help="Seconds to wait for the run")] = None,
    work_dir: Annotated[Path | None, typer.Option(help="Working directory (default: temp)")] = None,
    dev_trainer_cmd: Annotated[str | None, typer.Option(help="local only: trainer command (dev)")] = None,
    stage: Annotated[
        list[str] | None,
        typer.Option(
            "--stage",
            help="keys | data | contract | train | model | all (repeatable or comma list; default all). "
            "Stages resume from --work-dir/state.json",
        ),
    ] = None,
) -> None:
    """Idempotent: per TDP create-key, policy, prepare, encrypt, upload, publish; TDC propose; signatures;
    job submit; wait; receipt verification; model fetch + decrypt. Prints a summary JSON."""
    path = env_file or STATE.env
    if path is None and platform != "local":
        raise fail("--env depa-env.yaml is required (only --platform local can use the dev profile)", 2)
    if platform not in ("aks", "gcp", "local"):
        raise fail("--platform must be aks, gcp or local", 2)
    if prepare not in ("auto", "always", "never"):
        raise fail("--prepare must be auto, always or never", 2)
    directory = Path(name) if Path(name).is_dir() else scenarios_root / name
    if not (directory / "contract.yaml").exists():
        raise fail(f"{directory} is not a scenario (no contract.yaml)", 2)
    log = lambda m: sys.stderr.write(f"[deploy] {m}\n")  # noqa: E731
    if path is None:  # development: the `depa dev up` profile stands in for an environment file
        env = environment_from_profile(STATE.profile(), scenarios=[directory])
        dc_setup(env, env.profile(STATE.home or depa_home()), log=log)
    else:
        env = load_env(path)
    profile = env.profile(STATE.home or depa_home())
    stages = parse_stages(stage or [])
    if stages is not None and work_dir is None:
        raise fail("--work-dir is required with --stage (it holds the resumable state)", 2)
    opts = DeployOptions(
        stages=stages,
        platform=platform,
        parties=None if parties == "all" else frozenset(p.strip() for p in parties.split(",") if p.strip()),
        storage_token=read_env_secret(storage_token_env),
        output_token=read_env_secret(output_token_env),
        prepare=prepare,
        run=run,
        wait_timeout_s=wait_timeout,
        trainer_cmd=dev_trainer_cmd,
        work_dir=work_dir,
    )
    summary = deploy_scenario(directory, env, profile, opts, log=log)
    emit(summary)


def _scenario_dir(name: str, scenarios_root: Path) -> Path:
    directory = Path(name) if Path(name).is_dir() else scenarios_root / name
    if not (directory / "contract.yaml").exists():
        raise fail(f"{directory} is not a scenario (no contract.yaml)", 2)
    return directory


@app.command("teardown")
@handle_errors
def teardown(
    name: Annotated[str, typer.Argument(help="Scenario name (scenarios/<name>) or a path")],
    env_file: Annotated[Path | None, typer.Option("--env", help="depa-env.yaml (or the global --env)")] = None,
    scenarios_root: Annotated[Path, typer.Option(help="Directory containing the scenarios")] = Path("scenarios"),
    work_dir: Annotated[Path | None, typer.Option(help="Deploy work dir (its state.json is cleared)")] = None,
    keep_outputs: Annotated[bool, typer.Option(help="Keep the encrypted outputs in the TDC storage")] = False,
    output_token_env: Annotated[
        str | None, typer.Option(help="Env var with a bearer token for the TDC output storage")
    ] = None,
) -> None:
    """Remove only this scenario (idempotent): the TDC revokes its live contracts and deletes their outputs.
    Collaborators, DC services, datasets, keys and policies stay; a re-run proposes a new contract."""
    path = env_file or STATE.env
    if path is None:
        raise fail("--env depa-env.yaml is required", 2)
    env = load_env(path)
    summary = teardown_scenario(
        _scenario_dir(name, scenarios_root),
        env,
        env.profile(STATE.home or depa_home()),
        TeardownOptions(work_dir=work_dir, keep_outputs=keep_outputs, output_token=read_env_secret(output_token_env)),
        log=lambda m: sys.stderr.write(f"[teardown] {m}\n"),
    )
    emit(summary)


@app.command("check")
@handle_errors
def check(
    name: Annotated[str, typer.Argument(help="Scenario name (scenarios/<name>) or a path")],
    env_file: Annotated[Path | None, typer.Option("--env", help="depa-env.yaml (or the global --env)")] = None,
    platform: Annotated[str, typer.Option(help="aks | gcp | local")] = "aks",
    scenarios_root: Annotated[Path, typer.Option(help="Directory containing the scenarios")] = Path("scenarios"),
) -> None:
    """Read-only preflight: storage for every mapped provider, outputs for the TDC, compute and a CCR
    release for the platform. Exit 2 names the missing collaborator or artefact."""
    path = env_file or STATE.env
    if path is None:
        raise fail("--env depa-env.yaml is required", 2)
    result = check_scenario(_scenario_dir(name, scenarios_root), load_env(path), platform)
    emit(result, f"{name} on {result['platform']}: ready; parties {result['party_map']}")
