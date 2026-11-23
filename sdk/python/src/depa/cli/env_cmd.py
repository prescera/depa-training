"""``depa env``: the environment file generated from Terraform outputs."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from depa.cli.common import EXIT_VERIFY, emit, handle_errors
from depa.env import env_problems

app = typer.Typer(help="Environment file (depa-env.yaml): validate.", no_args_is_help=True)


@app.command("validate")
@handle_errors
def validate(path: Annotated[Path, typer.Argument(help="depa-env.yaml")]) -> None:
    """Schema and consistency checks (services, certs, parties, storage/outputs, releases)."""
    errors, warnings = env_problems(path)
    lines = [f"ERROR  {e}" for e in errors] + [f"WARN   {w}" for w in warnings]
    lines.append("valid" if not errors else "INVALID")
    emit({"ok": not errors, "errors": errors, "warnings": warnings}, "\n".join(lines))
    if errors:
        raise typer.Exit(EXIT_VERIFY)
