"""Trainer sandbox (spec §12 step 4).

The trainer (``depa-train run --recipe --inputs --outputs --result``) runs as a separate,
unprivileged process: a dedicated uid when the agent runs as root (the agent itself is made
non-dumpable so the trainer cannot ptrace it or read its memory), a scrubbed environment
without any token or key, read-only inputs owned by root, a single writable output directory,
its own session (so the whole process group can be killed) and a hard deadline. stdout/stderr
are discarded except on the ``virtual`` platform at ``debug`` level. Network isolation is
applied when the kernel permits unprivileged user+network namespaces.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import pwd
import resource
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from depa.errors import ConfigError

TRAINER_USER = "depa-trainer"
FALLBACK_UID = 10001
KILL_GRACE_S = 10
IMAGE_TRAINER_PREFIX = "/opt/depa/"
_TRAINER_CANDIDATES = ("/usr/local/bin/depa-train", "/usr/bin/depa-train")
_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


@dataclass(frozen=True)
class SandboxLayout:
    root: Path

    @property
    def inputs(self) -> Path:
        return self.root / "inputs"

    @property
    def outputs(self) -> Path:
        return self.root / "outputs"

    @property
    def result_dir(self) -> Path:
        return self.root / "result"

    @property
    def result(self) -> Path:
        return self.result_dir / "result.json"

    @property
    def recipe(self) -> Path:
        return self.root / "recipe.json"

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    @property
    def sealed(self) -> Path:
        return self.root / "sealed"


@dataclass(frozen=True)
class TrainerIdentity:
    uid: int
    gid: int
    dropped: bool


@dataclass(frozen=True)
class TrainerOutcome:
    returncode: int | None
    timed_out: bool
    seconds: float


def harden_agent_process() -> None:
    """No core dumps; not dumpable (blocks ptrace and /proc/<pid>/mem from other uids)."""
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    libc_name = ctypes.util.find_library("c")
    if libc_name:
        libc = ctypes.CDLL(libc_name, use_errno=True)
        pr_set_dumpable = 4
        libc.prctl(pr_set_dumpable, 0, 0, 0, 0)


def trainer_identity(*, require_drop: bool) -> TrainerIdentity:
    if os.geteuid() == 0:
        try:
            entry = pwd.getpwnam(TRAINER_USER)
            uid, gid = entry.pw_uid, entry.pw_gid
        except KeyError:
            uid = gid = FALLBACK_UID
        if uid == 0 or gid == 0:
            raise ConfigError("the trainer user must not be root")
        return TrainerIdentity(uid, gid, True)
    if require_drop:
        raise ConfigError("the CCR agent must run as root to drop trainer privileges", code="not_root")
    return TrainerIdentity(os.geteuid(), os.getegid(), False)


def resolve_trainer_command(dev_override: str | None, image_trainer: str | None = None) -> list[str]:
    """``depa-train`` from fixed locations (never the operator-controlled PATH).

    ``image_trainer`` (``DEPA_CCR_TRAINER``, set by the CCR image) must be an absolute executable
    path under ``/opt/depa/``; ``dev_override`` (``DEPA_TRAINER_CMD``) is honoured only on the
    ``virtual`` platform.
    """
    if dev_override:
        return dev_override.split()
    if image_trainer:
        path = Path(image_trainer)
        if not path.is_absolute() or ".." in path.parts or not str(path).startswith(IMAGE_TRAINER_PREFIX):
            raise ConfigError(f"DEPA_CCR_TRAINER must be an absolute path under {IMAGE_TRAINER_PREFIX}")
        if not os.access(path, os.X_OK):
            raise ConfigError("DEPA_CCR_TRAINER is not executable", code="no_trainer")
        return [str(path)]
    candidates = [str(Path(sys.prefix) / "bin" / "depa-train"), *_TRAINER_CANDIDATES]
    for candidate in candidates:
        if os.access(candidate, os.X_OK):
            return [candidate]
    raise ConfigError("depa-train is not installed in the CCR image", code="no_trainer")


def prepare_layout(layout: SandboxLayout, ident: TrainerIdentity) -> None:
    layout.root.mkdir(parents=True, exist_ok=True)
    os.chmod(layout.root, 0o755)  # noqa: S103 - traversable by the trainer uid
    layout.inputs.mkdir(mode=0o755, exist_ok=True)
    for d in (layout.outputs, layout.result_dir, layout.home, layout.tmp):
        d.mkdir(mode=0o700, exist_ok=True)
        if ident.dropped:
            os.chown(d, ident.uid, ident.gid)
    layout.sealed.mkdir(mode=0o700, exist_ok=True)


def make_read_only(root: Path) -> None:
    """Inputs: files 0444, directories 0555 (owned by the agent's uid, i.e. root in production)."""
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            os.chmod(Path(dirpath) / name, 0o444, follow_symlinks=False)
        for name in dirnames:
            os.chmod(Path(dirpath) / name, 0o555, follow_symlinks=False)  # noqa: S103 - read-only inputs
    os.chmod(root, 0o555)  # noqa: S103 - read-only inputs


def scrubbed_env(layout: SandboxLayout) -> dict[str, str]:
    return {
        "PATH": _PATH,
        "HOME": str(layout.home),
        "TMPDIR": str(layout.tmp),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }


def _isolate_network() -> None:
    # os.unshare exists on Linux with Python >= 3.12; the probe treats absence as unavailable.
    unshare = getattr(os, "unshare", None)
    if unshare is None:
        raise OSError("unshare is not available")
    unshare(os.CLONE_NEWUSER | os.CLONE_NEWNET)  # type: ignore[attr-defined]


def network_isolation_available(ident: TrainerIdentity) -> bool:
    """Probe whether the trainer can be placed in fresh user+network namespaces."""
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-c", "pass"],
            preexec_fn=_isolate_network,
            user=ident.uid if ident.dropped else None,
            group=ident.gid if ident.dropped else None,
            extra_groups=[] if ident.dropped else None,
            env={"PATH": _PATH},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return proc.returncode == 0


def run_trainer(
    command: Sequence[str],
    layout: SandboxLayout,
    ident: TrainerIdentity,
    *,
    timeout_s: float,
    isolate_network: bool,
    passthrough_output: bool,
    clock: Callable[[], float],
) -> TrainerOutcome:
    argv = [
        *command,
        "run",
        "--recipe",
        str(layout.recipe),
        "--inputs",
        str(layout.inputs),
        "--outputs",
        str(layout.outputs),
        "--result",
        str(layout.result),
    ]
    sink = None if passthrough_output else subprocess.DEVNULL
    start = clock()
    if timeout_s <= 0:
        return TrainerOutcome(None, True, 0.0)
    proc = subprocess.Popen(
        argv,
        cwd=layout.home,
        env=scrubbed_env(layout),
        stdin=subprocess.DEVNULL,
        stdout=sys.stderr if sink is None else sink,
        stderr=sys.stderr if sink is None else sink,
        user=ident.uid if ident.dropped else None,
        group=ident.gid if ident.dropped else None,
        extra_groups=[] if ident.dropped else None,
        umask=0o077,
        start_new_session=True,
        close_fds=True,
        preexec_fn=_isolate_network if isolate_network else None,  # noqa: PLW1509 - single-threaded agent
    )
    try:
        returncode = proc.wait(timeout=timeout_s)
        return TrainerOutcome(returncode, False, clock() - start)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        return TrainerOutcome(None, True, clock() - start)
    finally:
        if proc.poll() is None:
            _kill_group(proc)


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=KILL_GRACE_S)
        except subprocess.TimeoutExpired:
            continue
        return


def wipe(root: Path) -> None:
    """Remove the work directory contents (restoring write permission on read-only inputs)."""
    if not root.exists():
        return
    for dirpath, dirnames, _ in os.walk(root):
        for name in dirnames:
            p = Path(dirpath) / name
            if not p.is_symlink():
                os.chmod(p, 0o700)
    os.chmod(root, 0o700)
    for child in root.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
