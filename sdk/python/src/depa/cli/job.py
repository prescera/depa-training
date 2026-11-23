"""``depa job``: submit CCR jobs (AKS virtual nodes v2, GCP Confidential Space, local) and follow them."""

from __future__ import annotations

import tempfile
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from depa.cli.common import EXIT_ERROR, STATE, emit, fail, handle_errors
from depa.contract.recipe import select_recipe
from depa.deploy import aks, gcp, local
from depa.deploy.release import ReleaseManifest, load_release_manifest
from depa.deploy.spec import JobSpec
from depa.errors import ValidationError
from depa.workflows import build_trust_bundle

app = typer.Typer(help="Clean-room jobs: submit, status, logs.", no_args_is_help=True)


class Platform(StrEnum):
    aks = "aks"
    gcp = "gcp"
    local = "local"


_PLATFORM_IDS = {Platform.aks: "aci-snp", Platform.gcp: "gcp-cs-tdx", Platform.local: "virtual"}


@app.command("submit")
@handle_errors
def submit(
    platform: Annotated[Platform, typer.Option(help="aks (aci-snp) | gcp (gcp-cs-tdx) | local (virtual)")],
    contract_id: Annotated[str, typer.Option(help="Active contract id")],
    recipe_digest: Annotated[
        str | None, typer.Option(help="Recipe digest (default: the contract's only recipe)")
    ] = None,
    release: Annotated[Path | None, typer.Option(help="ccr/releases/<release_id>.json (aks, gcp)")] = None,
    log_level: Annotated[str, typer.Option(help="error|warn|info|debug (debug output only on virtual)")] = "info",
    namespace: Annotated[str, typer.Option(help="aks: Kubernetes namespace")] = "depa",
    context: Annotated[str | None, typer.Option(help="aks: kubectl context")] = None,
    project: Annotated[str | None, typer.Option(help="gcp: project id")] = None,
    zone: Annotated[str | None, typer.Option(help="gcp: zone, e.g. asia-south1-a")] = None,
    service_account: Annotated[str | None, typer.Option(help="gcp: workload service account")] = None,
    machine_type: Annotated[str, typer.Option(help="gcp: C3 machine type")] = "c3-standard-4",
    debug_image: Annotated[
        bool, typer.Option(help="gcp: confidential-space-debug image (never for real data)")
    ] = False,
    image: Annotated[
        str | None, typer.Option(help="local: run this CCR image under docker instead of in-process")
    ] = None,
    work_dir: Annotated[Path | None, typer.Option(help="local: CCR work directory (default: a temp dir)")] = None,
    dev_trainer_cmd: Annotated[
        str | None, typer.Option(help="local, in-process only: trainer command instead of depa-train")
    ] = None,
) -> None:
    """Deploy one CCR run of an active contract. The CCR re-derives everything from the contract."""
    profile = STATE.profile()
    kms, cs = profile.kms_client(), profile.cs_client()
    record = cs.get_contract(contract_id)
    contract = record["contract"]
    if record.get("state") != "active":
        raise ValidationError(f"contract is {record.get('state')}, not active", code="contract_not_active")
    recipes = contract["computation"]["recipes"]
    if recipe_digest is None:
        if len(recipes) != 1:
            raise ValidationError("contract has several recipes; pass --recipe-digest", code="ambiguous_recipe")
        recipe_digest = str(recipes[0]["digest"])
    select_recipe(contract, recipe_digest)
    platform_id = _PLATFORM_IDS[platform]
    if platform_id not in contract["computation"]["platforms"]:
        raise ValidationError(f"contract does not allow platform {platform_id}", code="platform_not_allowed")
    max_minutes = int(contract["computation"]["max_duration_minutes"])
    spec = JobSpec(
        contract_id=contract_id,
        recipe_digest=recipe_digest,
        cs_url=cs.base_url,
        kms_url=kms.base_url,
        platform=platform_id,
        max_duration_minutes=max_minutes,
        trust_bundle=build_trust_bundle(profile, kms, cs),
        log_level=log_level,
    ).ensure_name()
    if platform == Platform.local:
        _submit_local(spec, image, work_dir, dev_trainer_cmd)
        return
    if release is None:
        raise fail("--release is required for aks and gcp", 2)
    manifest = load_release_manifest(release)
    _check_release(manifest, contract, platform_id)
    if platform == Platform.aks:
        name = aks.submit(
            spec, manifest, namespace=namespace, context=context, contract_max_duration_minutes=max_minutes
        )
    else:
        if not (project and zone and service_account):
            raise fail("--project, --zone and --service-account are required for gcp", 2)
        name = gcp.submit(
            spec,
            manifest,
            project=project,
            zone=zone,
            service_account=service_account,
            contract_max_duration_minutes=max_minutes,
            machine_type=machine_type,
            debug=debug_image,
        )
    emit(
        {"name": name, "platform": platform_id, "contract_id": contract_id},
        f"submitted {name} ({platform_id})",
    )


def _check_release(manifest: ReleaseManifest, contract: dict[str, object], platform_id: str) -> None:
    computation = contract["computation"]
    assert isinstance(computation, dict)  # noqa: S101 - validated contract
    if manifest.platform != platform_id:
        raise ValidationError(f"release is for {manifest.platform}, not {platform_id}", code="release_platform")
    if manifest.release_id not in computation.get("ccr_releases", []):
        raise ValidationError("release is not listed in the contract's ccr_releases", code="release_not_in_contract")
    families = {r["document"].get("family") for r in computation["recipes"]}
    if not families <= set(manifest.families):
        raise ValidationError("release does not contain the recipe's trainer family", code="release_family")


def _submit_local(spec: JobSpec, image: str | None, work_dir: Path | None, trainer_cmd: str | None) -> None:
    env_extra = None
    if image is None:
        wd = work_dir or Path(tempfile.mkdtemp(prefix="depa-ccr-"))
        env_extra = {"DEPA_WORK_DIR": str(wd)}
        if trainer_cmd:
            env_extra["DEPA_TRAINER_CMD"] = trainer_cmd
    elif trainer_cmd:
        raise fail("--dev-trainer-cmd is only supported for in-process local runs", 2)
    job = local.run_local(spec, image=image, env_extra=env_extra)
    stderr = job.stderr.decode("utf-8", "replace")
    emit(
        {"name": job.name, "returncode": job.returncode, "log": stderr.splitlines()},
        f"{job.name}: exit {job.returncode}\n{stderr}",
    )
    if not job.succeeded:
        raise typer.Exit(EXIT_ERROR)


@app.command("status")
@handle_errors
def status(
    name: Annotated[str, typer.Argument()],
    platform: Annotated[Platform, typer.Option()],
    namespace: Annotated[str, typer.Option()] = "depa",
    context: Annotated[str | None, typer.Option()] = None,
    project: Annotated[str | None, typer.Option()] = None,
    zone: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Job / instance / container state."""
    if platform == Platform.aks:
        emit(aks.status(name, namespace=namespace, context=context))
    elif platform == Platform.gcp:
        if not (project and zone):
            raise fail("--project and --zone are required", 2)
        emit(gcp.status(name, project=project, zone=zone))
    else:
        emit(local.status(name))


@app.command("logs")
@handle_errors
def logs(
    name: Annotated[str, typer.Argument()],
    platform: Annotated[Platform, typer.Option()],
    namespace: Annotated[str, typer.Option()] = "depa",
    context: Annotated[str | None, typer.Option()] = None,
    project: Annotated[str | None, typer.Option()] = None,
    zone: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Allowlisted status lines (production releases disable stdio; the CS run record is authoritative)."""
    if platform == Platform.aks:
        text = aks.logs(name, namespace=namespace, context=context)
    elif platform == Platform.gcp:
        if not (project and zone):
            raise fail("--project and --zone are required", 2)
        text = gcp.logs(name, project=project, zone=zone)
    else:
        text = local.logs(name)
    emit({"name": name, "log": text.splitlines()}, text)
