"""In-process runtime guard (defence in depth on top of the clean-room sandbox).

Installs a CPython audit hook (PEP 578) that, for the rest of the process lifetime:

* forbids unpickling of any class or function (``pickle.find_class``), whatever library tries it;
* forbids network activity (connect/bind/DNS/send) and URL opening;
* forbids spawning processes, unless the recipe is tier 3 (custom entrypoint);
* forbids creating, modifying or deleting files outside the declared output directories, the
  scratch directory and the result file.

Audit hooks cannot be removed once installed. Native code that bypasses the Python runtime
(C++/Rust file or socket I/O) is not covered by the hook; those paths are closed by plugin-level
controls (e.g. DuckDB with external access disabled) and by the clean-room sandbox itself.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable
from pathlib import Path

from ..errors import SafetyError


class GuardViolation(SafetyError):
    """A forbidden operation was attempted while the runtime guard was active."""


_NETWORK_EVENTS = frozenset(
    {
        "socket.connect",
        "socket.bind",
        "socket.getaddrinfo",
        "socket.gethostbyname",
        "socket.gethostbyaddr",
        "socket.sendto",
        "socket.sendmsg",
        "urllib.Request",
        "webbrowser.open",
        "ftplib.connect",
        "smtplib.connect",
        "http.client.connect",
    }
)
_PROCESS_EVENTS = frozenset(
    {
        "subprocess.Popen",
        "os.system",
        "os.exec",
        "os.posix_spawn",
        "os.spawn",
        "os.fork",
        "os.forkpty",
        "pty.spawn",
    }
)
_PATH_EVENTS = {  # event -> indices of path arguments that must stay inside writable roots
    "os.mkdir": (0,),
    "os.remove": (0,),
    "os.rmdir": (0,),
    "os.rename": (0, 1),
    "os.truncate": (0,),
    "os.chmod": (0,),
    "os.symlink": (1,),
    "os.link": (1,),
    "shutil.rmtree": (0,),
    "shutil.copyfile": (1,),
    "shutil.move": (0, 1),
}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC

_state: dict[str, object] = {"installed": False}
# (event, detail, hard). Hard violations fail the run even if a library swallowed the exception.
violations: list[tuple[str, str, bool]] = []


def _is_write(mode: object, flags: object) -> bool:
    if isinstance(mode, str) and any(c in mode for c in "wax+"):
        return True
    return isinstance(flags, int) and bool(flags & _WRITE_FLAGS)


def install(*, write_roots: Iterable[Path], write_files: Iterable[Path], allow_subprocess: bool) -> None:
    """Install the guard. Idempotent per process; the first configuration wins."""
    if _state["installed"]:
        return
    roots = tuple(os.path.realpath(p) for p in write_roots)
    files = frozenset(os.path.realpath(p) for p in write_files) | {os.devnull}

    def allowed(path: object) -> bool:
        if isinstance(path, int):  # file descriptor, already opened
            return True
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        if not isinstance(path, str | os.PathLike):
            return False
        real = os.path.realpath(path)
        return real in files or any(real == r or real.startswith(r + os.sep) for r in roots)

    def deny(event: str, detail: str, exc_type: type[BaseException] = GuardViolation) -> None:
        violations.append((event, detail, exc_type is GuardViolation))
        raise exc_type(f"runtime guard: {event} is forbidden ({detail})")

    def hook(event: str, args: tuple) -> None:
        if event == "pickle.find_class":
            deny(event, f"{args[0]}.{args[1]}")
        elif event in _NETWORK_EVENTS:
            deny(event, "network access")
        elif event in _PROCESS_EVENTS and not allow_subprocess:
            deny(event, "process creation")
        elif event == "open":
            path, mode, flags = (*args, None, None, None)[:3]
            if _is_write(mode, flags) and not allowed(path):
                # PermissionError lets libraries that probe optional caches degrade gracefully.
                deny(event, "write outside allowed directories", PermissionError)
        elif event in _PATH_EVENTS:
            for idx in _PATH_EVENTS[event]:
                if idx < len(args) and not allowed(args[idx]):
                    deny(event, "filesystem change outside allowed directories", PermissionError)

    sys.dont_write_bytecode = True
    sys.addaudithook(hook)
    _state["installed"] = True


def hard_violations() -> list[str]:
    """Pickle, network and process violations: they fail the run even if the exception was swallowed."""
    return [f"{event}: {detail}" for event, detail, hard in violations if hard]
