"""AKS virtual nodes v2 submitter (platform ``aci-snp``).

Renders ``templates/aks_vn2_job.yaml`` into a ``batch/v1`` Job and submits it with
``kubectl create -f -`` (``create`` rather than ``apply``: a CCR job is one-shot and must never
silently reconfigure an existing object of the same name).
"""

from __future__ import annotations

import json
import re
from importlib import resources
from string import Template
from typing import Any

import yaml

from depa.core.encoding import loads_object
from depa.deploy import DeployError, Runner, run_checked, subprocess_runner
from depa.deploy.release import ReleaseManifest
from depa.deploy.spec import JobSpec
from depa.errors import ValidationError

TEMPLATE_NAME = "aks_vn2_job.yaml"
CONTAINER_NAME = "ccr"
COMMAND = ["depa-ccr", "run"]
WORKDIR = "/depa"
_NAMESPACE_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
_CONTEXT_RE = re.compile(r"^[A-Za-z0-9._@:/-]{1,253}$")


def _template() -> Template:
    text = resources.files("depa.deploy").joinpath("templates", TEMPLATE_NAME).read_text("utf-8")
    return Template(text)


def _quantity(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def _check_inputs(spec: JobSpec, manifest: ReleaseManifest, namespace: str, contract_max_duration_minutes: int) -> None:
    if spec.platform != "aci-snp" or manifest.platform != "aci-snp":
        raise ValidationError("the AKS submitter only deploys aci-snp releases", code="bad_platform")
    if not _NAMESPACE_RE.fullmatch(namespace):
        raise ValidationError(f"invalid namespace {namespace!r}", code="bad_job_spec")
    spec.check_duration(contract_max_duration_minutes)
    manifest.check_env(spec.env())
    if not manifest.cce_policy_b64:
        raise ValidationError("aci-snp release has no CCE policy", code="bad_release_manifest")


def _validate_rendered(job: Any, spec: JobSpec, manifest: ReleaseManifest) -> dict[str, Any]:
    """Re-check the parsed object: nothing outside the container contract may appear."""

    def fail(what: str) -> DeployError:
        return DeployError(f"rendered job violates the container contract: {what}", code="bad_render")

    if not isinstance(job, dict) or job.get("kind") != "Job" or job.get("apiVersion") != "batch/v1":
        raise fail("not a batch/v1 Job")
    job_spec = job["spec"]
    if job_spec.get("backoffLimit") != 0 or job_spec.get("activeDeadlineSeconds") != spec.max_duration_minutes * 60:
        raise fail("backoffLimit / activeDeadlineSeconds")
    pod = job_spec["template"]["spec"]
    if pod.get("restartPolicy") != "Never" or pod.get("automountServiceAccountToken") is not False:
        raise fail("restartPolicy / service account token")
    if "volumes" in pod or "initContainers" in pod or pod.get("hostNetwork") or pod.get("hostPID"):
        raise fail("volumes / init containers / host namespaces")
    containers = pod.get("containers")
    if not isinstance(containers, list) or len(containers) != 1:
        raise fail("exactly one container is required")
    c = containers[0]
    if c.get("name") != CONTAINER_NAME or c.get("image") != manifest.image or c.get("command") != COMMAND:
        raise fail("container name / image / command")
    if c.get("workingDir") != WORKDIR or "volumeMounts" in c or "args" in c or "envFrom" in c:
        raise fail("workingDir / mounts / args / envFrom")
    sc = c.get("securityContext", {})
    if sc.get("privileged") is not False or sc.get("allowPrivilegeEscalation") is not False:
        raise fail("securityContext")
    env = c.get("env")
    if (
        not isinstance(env, list)
        or {e.get("name"): e.get("value") for e in env} != spec.env()
        or len(env) != len(spec.env())
    ):
        raise fail("env differs from the job spec")
    annotations = job_spec["template"]["metadata"]["annotations"]
    if annotations.get("microsoft.containerinstance.virtualnode.ccepolicy") != manifest.cce_policy_b64:
        raise fail("CCE policy annotation")
    return job


def render_job(
    spec: JobSpec,
    manifest: ReleaseManifest,
    *,
    namespace: str,
    contract_max_duration_minutes: int,
) -> dict[str, Any]:
    """Render and validate the Job object. ``spec.name`` must be set (see ``JobSpec.ensure_name``)."""
    if spec.name is None:
        raise ValidationError("render_job needs a named JobSpec (call ensure_name())", code="bad_job_spec")
    _check_inputs(spec, manifest, namespace, contract_max_duration_minutes)
    env = [{"name": k, "value": v} for k, v in spec.env().items()]
    values = {
        "name": spec.name,
        "namespace": namespace,
        "contract_id": spec.contract_id,
        "release_id": manifest.release_id,
        "active_deadline_seconds": spec.max_duration_minutes * 60,
        "cce_policy": manifest.cce_policy_b64,
        "image": manifest.image,
        "env": env,
        "cpu": _quantity(manifest.cpu),
        "memory": f"{_quantity(manifest.memory_gb)}Gi",
    }
    rendered = _template().substitute({k: json.dumps(v, ensure_ascii=True) for k, v in values.items()})
    return _validate_rendered(yaml.safe_load(rendered), spec, manifest)


def render_job_yaml(
    spec: JobSpec,
    manifest: ReleaseManifest,
    *,
    namespace: str,
    contract_max_duration_minutes: int,
) -> str:
    job = render_job(spec, manifest, namespace=namespace, contract_max_duration_minutes=contract_max_duration_minutes)
    return yaml.safe_dump(job, sort_keys=False, default_flow_style=False, width=4096)


def _kubectl(context: str | None, namespace: str) -> list[str]:
    if not _NAMESPACE_RE.fullmatch(namespace):
        raise ValidationError(f"invalid namespace {namespace!r}", code="bad_job_spec")
    argv = ["kubectl"]
    if context is not None:
        if not _CONTEXT_RE.fullmatch(context):
            raise ValidationError("invalid kubectl context name", code="bad_job_spec")
        argv += ["--context", context]
    return [*argv, "--namespace", namespace]


def _check_name(name: str) -> str:
    if not _NAMESPACE_RE.fullmatch(name):
        raise ValidationError(f"invalid job name {name!r}", code="bad_job_spec")
    return name


def submit(
    spec: JobSpec,
    manifest: ReleaseManifest,
    *,
    namespace: str,
    contract_max_duration_minutes: int,
    context: str | None = None,
    runner: Runner = subprocess_runner,
) -> str:
    """Create the Job; returns its name."""
    spec = spec.ensure_name()
    document = render_job_yaml(
        spec, manifest, namespace=namespace, contract_max_duration_minutes=contract_max_duration_minutes
    )
    run_checked(
        runner,
        [*_kubectl(context, namespace), "create", "-f", "-"],
        stdin=document.encode("utf-8"),
        what="kubectl create",
    )
    return spec.job_name()


def summarize_job(obj: dict[str, Any]) -> dict[str, Any]:
    status = obj.get("status") or {}
    conditions = [
        {k: c.get(k) for k in ("type", "status", "reason", "message")} for c in status.get("conditions") or []
    ]
    succeeded, failed, active = (
        int(status.get("succeeded") or 0),
        int(status.get("failed") or 0),
        int(status.get("active") or 0),
    )
    if succeeded:
        state = "succeeded"
    elif failed or any(c["type"] == "Failed" and c["status"] == "True" for c in conditions):
        state = "failed"
    elif active:
        state = "running"
    else:
        state = "pending"
    return {
        "name": (obj.get("metadata") or {}).get("name"),
        "state": state,
        "active": active,
        "succeeded": succeeded,
        "failed": failed,
        "start_time": status.get("startTime"),
        "completion_time": status.get("completionTime"),
        "conditions": conditions,
    }


def status(
    name: str, *, namespace: str, context: str | None = None, runner: Runner = subprocess_runner
) -> dict[str, Any]:
    result = run_checked(
        runner,
        [*_kubectl(context, namespace), "get", "job", _check_name(name), "-o", "json"],
        what="kubectl get job",
    )
    return summarize_job(loads_object(result.stdout))


def logs(name: str, *, namespace: str, context: str | None = None, runner: Runner = subprocess_runner) -> str:
    """Container stdout (structured, allowlisted lines; empty on production releases with stdio disabled)."""
    result = run_checked(
        runner,
        [*_kubectl(context, namespace), "logs", f"job/{_check_name(name)}", "-c", CONTAINER_NAME],
        what="kubectl logs",
    )
    return result.stdout.decode("utf-8", "replace")
