"""GCP Confidential Space submitter (platform ``gcp-cs-tdx``): one C3 Intel TDX VM per run.

The VM runs the Confidential Space launcher, which pulls ``tee-image-reference`` by digest and
starts it once (``tee-restart-policy=Never``). ``--max-run-duration`` with
``--instance-termination-action=DELETE`` bounds its lifetime (spec §12). Settings follow the
proven TDX template of DEPA-inferencing: pd-balanced boot disk and no min CPU platform (C3
rejects both alternatives), Shielded VM with secure boot, ``TERMINATE`` on host maintenance.
The ``tee-env-*`` overrides require the image's launch policy to allow them
(``tee.launch_policy.allow_env_override``), which the release build sets.
"""

from __future__ import annotations

import re
from typing import Any

from depa.core.encoding import loads_object
from depa.deploy import DeployError, Runner, run_checked, subprocess_runner
from depa.deploy.release import ReleaseManifest
from depa.deploy.spec import JobSpec
from depa.errors import ValidationError

METADATA_DELIMITER = "~"
IMAGE_PROJECT = "confidential-space-images"
_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_ZONE_RE = re.compile(r"^[a-z]+-[a-z]+[0-9]+-[a-z]$")
_SA_RE = re.compile(r"^[a-z0-9-]+@[a-z0-9.-]+\.iam\.gserviceaccount\.com$")
_MACHINE_RE = re.compile(r"^c3-[a-z]+-[0-9]+$")
_RESOURCE_RE = re.compile(r"^[a-z0-9]([-a-z0-9/._]{0,200}[a-z0-9])?$")
_NAME_RE = re.compile(r"^[a-z]([-a-z0-9]{0,61}[a-z0-9])?$")


def _check(pattern: re.Pattern[str], value: str, what: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValidationError(f"invalid {what} {value!r}", code="bad_job_spec")
    return value


def _scratch_bytes(manifest: ReleaseManifest) -> int:
    return int(manifest.memory_gb * 1024**3) // 2


def _metadata(spec: JobSpec, manifest: ReleaseManifest, debug: bool) -> str:
    items = [
        ("tee-image-reference", manifest.image),
        ("tee-restart-policy", "Never"),
        ("tee-container-log-redirect", "true" if debug else "false"),
        # Plaintext scratch lives only in TDX-protected memory (spec §12): tmpfs at /depa, sized to
        # half the container memory (bytes). The image launch policy must allow this destination.
        ("tee-mount", f"type=tmpfs,source=tmpfs,destination=/depa,size={_scratch_bytes(manifest)}"),
        *((f"tee-env-{k}", v) for k, v in spec.env().items()),
    ]
    for key, value in items:
        if METADATA_DELIMITER in value or "\n" in value:
            raise ValidationError(f"metadata value for {key} contains a reserved character", code="bad_job_spec")
    return f"^{METADATA_DELIMITER}^" + METADATA_DELIMITER.join(f"{k}={v}" for k, v in items)


def build_create_argv(
    spec: JobSpec,
    manifest: ReleaseManifest,
    *,
    project: str,
    zone: str,
    service_account: str,
    contract_max_duration_minutes: int,
    machine_type: str = "c3-standard-4",
    debug: bool = False,
    network: str | None = None,
    subnet: str | None = None,
) -> list[str]:
    """``gcloud compute instances create`` argv. ``spec.name`` must be set."""
    if spec.platform != "gcp-cs-tdx" or manifest.platform != "gcp-cs-tdx":
        raise ValidationError("the GCP submitter only deploys gcp-cs-tdx releases", code="bad_platform")
    if spec.name is None:
        raise ValidationError("build_create_argv needs a named JobSpec (call ensure_name())", code="bad_job_spec")
    spec.check_duration(contract_max_duration_minutes)
    manifest.check_env(spec.env())
    argv = [
        "gcloud",
        "compute",
        "instances",
        "create",
        _check(_NAME_RE, spec.name, "instance name"),
        f"--project={_check(_PROJECT_RE, project, 'project')}",
        f"--zone={_check(_ZONE_RE, zone, 'zone')}",
        f"--machine-type={_check(_MACHINE_RE, machine_type, 'machine type (C3 is required for TDX)')}",
        "--confidential-compute-type=TDX",
        "--maintenance-policy=TERMINATE",
        "--provisioning-model=STANDARD",
        "--shielded-secure-boot",
        "--shielded-vtpm",
        "--shielded-integrity-monitoring",
        f"--image-project={IMAGE_PROJECT}",
        f"--image-family={'confidential-space-debug' if debug else 'confidential-space'}",
        "--boot-disk-type=pd-balanced",
        f"--service-account={_check(_SA_RE, service_account, 'service account')}",
        "--scopes=cloud-platform",
        f"--max-run-duration={spec.max_duration_minutes * 60}s",
        "--instance-termination-action=DELETE",
        "--no-restart-on-failure",
        "--no-address",  # no external IP; egress via the module's Cloud NAT
        f"--labels=app=depa-ccr,depa-contract-id={spec.contract_id}",
    ]
    if network is not None:
        argv.append(f"--network={_check(_RESOURCE_RE, network, 'network')}")
    if subnet is not None:
        argv.append(f"--subnet={_check(_RESOURCE_RE, subnet, 'subnet')}")
    argv += [f"--metadata={_metadata(spec, manifest, debug)}", "--format=json"]
    return argv


def submit(
    spec: JobSpec,
    manifest: ReleaseManifest,
    *,
    project: str,
    zone: str,
    service_account: str,
    contract_max_duration_minutes: int,
    machine_type: str = "c3-standard-4",
    debug: bool = False,
    network: str | None = None,
    subnet: str | None = None,
    runner: Runner = subprocess_runner,
) -> str:
    """Create the Confidential Space VM; returns the instance name."""
    spec = spec.ensure_name()
    argv = build_create_argv(
        spec,
        manifest,
        project=project,
        zone=zone,
        service_account=service_account,
        contract_max_duration_minutes=contract_max_duration_minutes,
        machine_type=machine_type,
        debug=debug,
        network=network,
        subnet=subnet,
    )
    run_checked(runner, argv, what="gcloud compute instances create")
    return spec.job_name()


def _instance_argv(verb: str, name: str, project: str, zone: str) -> list[str]:
    return [
        "gcloud",
        "compute",
        "instances",
        verb,
        _check(_NAME_RE, name, "instance name"),
        f"--project={_check(_PROJECT_RE, project, 'project')}",
        f"--zone={_check(_ZONE_RE, zone, 'zone')}",
    ]


def status(name: str, *, project: str, zone: str, runner: Runner = subprocess_runner) -> dict[str, Any]:
    """Instance state. A finished run deletes its VM, so "not found" is reported as ``deleted``."""
    result = runner([*_instance_argv("describe", name, project, zone), "--format=json"])
    if result.returncode != 0:
        if b"was not found" in result.stderr or b"notFound" in result.stderr:
            return {"name": name, "state": "deleted"}
        detail = result.stderr.decode("utf-8", "replace").strip()[:2000]
        raise DeployError(f"gcloud describe failed (exit {result.returncode}): {detail}")
    obj = loads_object(result.stdout)
    gce_status = str(obj.get("status", ""))
    state = {"PROVISIONING": "pending", "STAGING": "pending", "RUNNING": "running"}.get(gce_status, "stopped")
    return {
        "name": obj.get("name", name),
        "state": state,
        "status": gce_status,
        "created": obj.get("creationTimestamp"),
        "confidential_type": (obj.get("confidentialInstanceConfig") or {}).get("confidentialInstanceType"),
    }


def logs(name: str, *, project: str, zone: str, runner: Runner = subprocess_runner) -> str:
    """Serial console output. Container output only appears there with the debug image
    (``tee-container-log-redirect=true``); production runs show launcher lines only."""
    result = run_checked(
        runner,
        [*_instance_argv("get-serial-port-output", name, project, zone), "--port=1"],
        what="gcloud serial output",
    )
    return result.stdout.decode("utf-8", "replace")
