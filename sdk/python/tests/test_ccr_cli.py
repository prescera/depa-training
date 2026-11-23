"""``depa-ccr`` argument handling (the container entrypoint is ``["depa-ccr", "run"]``)."""

from __future__ import annotations

import pytest

from depa.ccr.cli import USAGE, main


@pytest.mark.parametrize("argv", [["--help"], ["-h"], ["run", "--help"], ["run", "-h"]])
def test_help_prints_usage_and_succeeds(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(argv) == 0
    assert capsys.readouterr().out == USAGE


@pytest.mark.parametrize("argv", [[], ["start"], ["run", "extra"]])
def test_unknown_arguments_are_usage_errors(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(argv) == 2
    assert capsys.readouterr().err == USAGE
