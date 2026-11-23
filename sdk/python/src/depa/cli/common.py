"""CLI plumbing: global options, profile resolution, output (human or ``--json``), exit codes.

Exit codes: 0 success; 1 unexpected error; 2 usage/configuration; 3 validation or
verification failure; 4 service/storage/deploy error; 5 not found.
"""

from __future__ import annotations

import functools
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

import typer

from depa.errors import DepaError
from depa.profile import Profile, depa_home

P = ParamSpec("P")
R = TypeVar("R")

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_VERIFY, EXIT_SERVICE, EXIT_NOT_FOUND = 0, 1, 2, 3, 4, 5


@dataclass
class State:
    json: bool = False
    home: Path | None = None
    party: str | None = None
    kms_url: str | None = None
    kms_cert: str | None = None
    cs_url: str | None = None
    cs_cert: str | None = None
    env: Path | None = None

    def profile(self) -> Profile:
        if self.env is not None:
            from depa.env import load_env  # noqa: PLC0415 - only when --env is used

            base = load_env(self.env).profile(self.home or depa_home(), self.party)
            return base.with_overrides(
                kms_url=self.kms_url, kms_cert=self.kms_cert, cs_url=self.cs_url, cs_cert=self.cs_cert
            )
        return Profile.load(self.home).with_overrides(
            party_id=self.party,
            kms_url=self.kms_url,
            kms_cert=self.kms_cert,
            cs_url=self.cs_url,
            cs_cert=self.cs_cert,
        )


STATE = State()


def emit(data: Any, human: str | None = None) -> None:
    """Print ``data`` as JSON with ``--json``; otherwise ``human`` (or pretty JSON)."""
    if STATE.json or human is None:
        sys.stdout.write(json.dumps(data, indent=2, sort_keys=True, default=str) + "\n")
    else:
        sys.stdout.write(human.rstrip() + "\n")


def fail(message: str, code: int = EXIT_ERROR, *, error_code: str = "error") -> typer.Exit:
    if STATE.json:
        sys.stdout.write(json.dumps({"ok": False, "error": error_code, "message": message}) + "\n")
    else:
        sys.stderr.write(f"error: {message}\n")
    return typer.Exit(code)


def handle_errors(fn: Callable[P, R]) -> Callable[P, R]:
    """Map SDK errors to messages and exit codes (never a traceback for expected failures)."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except DepaError as exc:
            raise fail(str(exc), exc.exit_code, error_code=exc.code) from exc
        except (OSError, ValueError) as exc:
            raise fail(f"{type(exc).__name__}: {exc}", EXIT_ERROR) from exc

    return wrapper


def read_env_secret(name: str | None) -> str | None:
    """Read a bearer token from the named environment variable (never from argv)."""
    if not name:
        return None
    import os  # noqa: PLC0415

    value = os.environ.get(name)
    if not value:
        raise fail(f"environment variable {name} is not set", EXIT_USAGE)
    return value
