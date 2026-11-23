"""``depa``: the DEPA Training v2 command line."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from depa import __version__
from depa.cli import contract, dataset, dc, dev, env_cmd, job, party, run, scenario
from depa.cli.common import STATE

app = typer.Typer(
    help="DEPA Training v2: parties, governance, datasets, contracts, clean-room jobs, receipts and models.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
app.add_typer(party.app, name="party")
app.add_typer(dc.app, name="dc")
app.add_typer(dataset.app, name="dataset")
app.add_typer(contract.app, name="contract")
app.add_typer(job.app, name="job")
app.add_typer(run.run_app, name="run")
app.add_typer(run.model_app, name="model")
app.add_typer(dev.app, name="dev")
app.add_typer(env_cmd.app, name="env")
app.add_typer(scenario.app, name="scenario")


def _version(value: bool) -> None:
    if value:
        typer.echo(f"depa {__version__}")
        raise typer.Exit


@app.callback()
def main_options(
    json_output: Annotated[bool, typer.Option("--json", help="Machine-readable JSON output")] = False,
    home: Annotated[Path | None, typer.Option(help="Profile directory (default $DEPA_HOME or ~/.depa)")] = None,
    party_id: Annotated[str | None, typer.Option("--party", help="Act as this party")] = None,
    kms_url: Annotated[str | None, typer.Option(help="KMS base URL")] = None,
    kms_cert: Annotated[str | None, typer.Option(help="Pinned KMS service certificate (PEM)")] = None,
    cs_url: Annotated[str | None, typer.Option(help="CS base URL")] = None,
    cs_cert: Annotated[str | None, typer.Option(help="Pinned CS service certificate (PEM)")] = None,
    env: Annotated[
        Path | None, typer.Option("--env", help="Environment file (depa-env.yaml) with services and keys")
    ] = None,
    version: Annotated[bool, typer.Option("--version", callback=_version, is_eager=True, help="Show version")] = False,
) -> None:
    STATE.json = json_output
    STATE.env = env
    STATE.home = home
    STATE.party = party_id
    STATE.kms_url, STATE.kms_cert, STATE.cs_url, STATE.cs_cert = kms_url, kms_cert, cs_url, cs_cert


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
