"""CCR job submitters: AKS virtual nodes v2 (``aci-snp``), GCP Confidential Space (``gcp-cs-tdx``)
and local development runs (``virtual``).

External tools (``kubectl``, ``gcloud``, ``docker``) are invoked through a ``Runner`` with fixed
argv lists, never through a shell, so tests can inject a fake runner.
"""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from depa.errors import DepaError, DeployCancelled


class DeployError(DepaError):
    code = "deploy_failed"
    exit_code = 4


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class Runner(Protocol):
    def __call__(
        self,
        argv: Sequence[str],
        *,
        stdin: bytes | None = None,
        env: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> RunResult: ...


def subprocess_runner(
    argv: Sequence[str],
    *,
    stdin: bytes | None = None,
    env: Mapping[str, str] | None = None,
    timeout_s: float | None = None,
) -> RunResult:
    """Default runner: ``subprocess.run`` without a shell, capturing output."""
    try:
        proc = subprocess.run(
            list(argv),
            input=stdin,
            env=dict(env) if env is not None else None,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError as exc:
        raise DeployError(f"required tool not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise DeployError(f"{argv[0]} timed out after {timeout_s}s", code="deploy_timeout") from exc
    return RunResult(proc.returncode, proc.stdout or b"", proc.stderr or b"")


def cancellable_runner(cancel: threading.Event, *, poll_s: float = 0.2, grace_s: float = 10.0) -> Runner:
    """A ``Runner`` whose child (and its process group) is terminated promptly once ``cancel`` is set.

    Output goes to temporary files (no pipe deadlocks); ``DeployCancelled`` is raised on cancel.
    """

    def run(
        argv: Sequence[str],
        *,
        stdin: bytes | None = None,
        env: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> RunResult:
        if cancel.is_set():
            raise DeployCancelled("cancelled before start")
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                proc = subprocess.Popen(
                    list(argv),
                    stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    env=dict(env) if env is not None else None,
                    start_new_session=True,
                )
            except FileNotFoundError as exc:
                raise DeployError(f"required tool not found: {argv[0]}") from exc
            if stdin is not None and proc.stdin is not None:
                proc.stdin.write(stdin)
                proc.stdin.close()
            deadline = None if timeout_s is None else time.monotonic() + timeout_s
            while proc.poll() is None:
                if cancel.wait(poll_s):
                    _terminate(proc, grace_s)
                    raise DeployCancelled(f"{argv[0]} cancelled")
                if deadline is not None and time.monotonic() > deadline:
                    _terminate(proc, grace_s)
                    raise DeployError(f"{argv[0]} timed out after {timeout_s}s", code="deploy_timeout")
            out.seek(0)
            err.seek(0)
            return RunResult(proc.returncode, out.read(), err.read())

    return run


def _terminate(proc: subprocess.Popen[bytes], grace_s: float) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            continue
        return


def run_checked(runner: Runner, argv: Sequence[str], *, stdin: bytes | None = None, what: str) -> RunResult:
    result = runner(argv, stdin=stdin)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()[:2000]
        raise DeployError(f"{what} failed (exit {result.returncode}): {detail}")
    return result
