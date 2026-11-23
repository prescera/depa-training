"""Local development runs (platform ``virtual`` only): the CCR agent as a subprocess, or the
CCR image under docker with host networking (so the sandbox services' pinned certificates,
issued for 127.0.0.1, still match). Container names carry the ``depa2-sdk-`` prefix.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from depa.core.encoding import loads_strict
from depa.deploy import DeployError, Runner, run_checked, subprocess_runner
from depa.deploy.spec import JobSpec
from depa.errors import ValidationError

CONTAINER_PREFIX = "depa2-sdk-ccr-"
_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,511}$")
_CONTAINER_RE = re.compile(r"^depa2-sdk-ccr-[a-z0-9-]{1,80}$")


@dataclass(frozen=True)
class LocalJob:
    name: str
    argv: tuple[str, ...]
    returncode: int
    stdout: bytes = field(repr=False)
    stderr: bytes = field(repr=False)

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0


def container_name(spec: JobSpec) -> str:
    """``depa2-sdk-ccr-<suffix>`` derived from the (named) job spec."""
    if spec.name is None:
        raise ValidationError("container_name needs a named JobSpec", code="bad_job_spec")
    suffix = spec.name.removeprefix("depa-ccr-")
    return f"{CONTAINER_PREFIX}{suffix}"


def build_argv(spec: JobSpec, image: str | None) -> list[str]:
    if image is None:
        return [sys.executable, "-m", "depa.ccr", "run"]
    if not _IMAGE_RE.fullmatch(image):
        raise ValidationError(f"invalid image reference {image!r}", code="bad_job_spec")
    argv = ["docker", "run", "--rm", "--name", container_name(spec), "--network", "host"]
    for key, value in spec.env().items():
        argv += ["-e", f"{key}={value}"]
    return [*argv, image, "depa-ccr", "run"]


def run_local(
    spec: JobSpec,
    *,
    image: str | None,
    runner: Runner = subprocess_runner,
    env_extra: Mapping[str, str] | None = None,
    timeout_s: float | None = None,
) -> LocalJob:
    """Run one CCR synchronously and return its outcome.

    Without ``image`` the agent runs as ``python -m depa.ccr run`` with a minimal environment
    (``PATH``, ``HOME``, the DEPA_* variables and ``env_extra``). With ``image`` only the
    DEPA_* variables reach the container, as on the confidential platforms.
    """
    if spec.platform != "virtual":
        raise ValidationError("local runs only support the virtual platform", code="bad_platform")
    spec = spec.ensure_name()
    argv = build_argv(spec, image)
    if image is None:
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/"),
            **spec.env(),
            **dict(env_extra or {}),
        }
        name = spec.name or ""
    else:
        if env_extra:
            raise ValidationError("env_extra is not supported for container runs", code="bad_job_spec")
        env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")}
        name = container_name(spec)
    result = runner(argv, env=env, timeout_s=timeout_s)
    return LocalJob(name, tuple(argv), result.returncode, result.stdout, result.stderr)


def _check_container(name: str) -> str:
    if not _CONTAINER_RE.fullmatch(name):
        raise ValidationError(f"not a depa2-sdk CCR container name: {name!r}", code="bad_job_spec")
    return name


def status(name: str, *, runner: Runner = subprocess_runner) -> dict[str, Any]:
    """State of a local CCR container (``gone`` once ``--rm`` removed it)."""
    result = runner(["docker", "inspect", "--type", "container", _check_container(name)])
    if result.returncode != 0:
        if b"No such" in result.stderr:
            return {"name": name, "state": "gone"}
        raise DeployError(f"docker inspect failed: {result.stderr.decode('utf-8', 'replace')[:500]}")
    parsed = loads_strict(result.stdout)
    if not isinstance(parsed, list) or not parsed or not isinstance(parsed[0], dict):
        raise DeployError("docker inspect returned an unexpected document")
    state = parsed[0].get("State") or {}
    return {
        "name": name,
        "state": str(state.get("Status", "unknown")),
        "exit_code": state.get("ExitCode"),
        "started_at": state.get("StartedAt"),
        "finished_at": state.get("FinishedAt"),
    }


def logs(name: str, *, runner: Runner = subprocess_runner) -> str:
    result = run_checked(runner, ["docker", "logs", _check_container(name)], what="docker logs")
    return (result.stdout + result.stderr).decode("utf-8", "replace")
