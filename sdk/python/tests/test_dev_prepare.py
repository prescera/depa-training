"""``depa dev scenario run`` prepares missing provider data, as ``scenario deploy --prepare auto`` does."""

from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from depa.dev.scenario import _prepare_missing
from depa.errors import ServiceError, ValidationError


def _template(dataset_id: str, path: Path) -> SimpleNamespace:
    return SimpleNamespace(dataset_id=dataset_id, source=SimpleNamespace(path=path))


def _script(scenario: Path, body: str) -> None:
    script = scenario / "prepare" / "prepare.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)


def test_runs_prepare_only_for_owners_with_missing_data(tmp_path: Path) -> None:
    present = tmp_path / "data" / "a"
    present.mkdir(parents=True)
    (present / "x.csv").write_text("1")
    _script(tmp_path, f'echo "$2" >> {tmp_path}/called\n')
    templates = [_template("a/ds", present), _template("b/ds", tmp_path / "data" / "b")]

    _prepare_missing(tmp_path, templates, log=lambda _msg: None)

    assert (tmp_path / "called").read_text().split() == ["b"]


def test_nothing_missing_needs_no_script(tmp_path: Path) -> None:
    present = tmp_path / "data"
    present.mkdir()
    (present / "x").write_text("1")
    _prepare_missing(tmp_path, [_template("a/ds", present)], log=lambda _msg: None)


def test_missing_script_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        _prepare_missing(tmp_path, [_template("a/ds", tmp_path / "nope")], log=lambda _msg: None)


def test_failing_prepare_raises(tmp_path: Path) -> None:
    _script(tmp_path, "echo boom >&2; exit 3\n")
    with pytest.raises(ServiceError, match="boom"):
        _prepare_missing(tmp_path, [_template("a/ds", tmp_path / "nope")], log=lambda _msg: None)
