"""DEPA_CCR_TRAINER resolution (the image's trainer venv)."""

from __future__ import annotations

import pytest

from depa.ccr.trainer import resolve_trainer_command
from depa.errors import ConfigError


def test_image_trainer_must_live_under_opt_depa(tmp_path) -> None:
    for bad in ("relative/depa-train", "/usr/bin/sh", "/opt/depa/../../bin/sh"):
        with pytest.raises(ConfigError):
            resolve_trainer_command(None, bad)
    with pytest.raises(ConfigError):
        resolve_trainer_command(None, "/opt/depa/trainer/bin/does-not-exist")
    assert resolve_trainer_command("python fake.py", "/opt/depa/x") == ["python", "fake.py"]
