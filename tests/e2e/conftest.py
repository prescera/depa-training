"""End-to-end fixtures.

* ``mock_stack``: the SDK's mock Data Collaborative served over real HTTPS (self-signed,
  pinned), configured through the same ``configure_dev_governance`` code path as ``depa dev up``.
* ``sandbox_stack``: the real KMS + CS CCF sandboxes (``depa dev up``) on ports
  ``DEPA_E2E_KMS_PORT``/``DEPA_E2E_CS_PORT`` (default 18160/18161). Skipped unless both service
  repositories provide ``scripts/sandbox-up.sh`` and docker is available, or when
  ``DEPA_E2E_SANDBOX=0``. Torn down (and purged) at the end of the session.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SDK_TESTS = REPO / "sdk" / "python" / "tests"
sys.path.insert(0, str(SDK_TESTS))

from depa.dev import stack
from depa.profile import Profile
from mockdc import MockDC

FIXTURE = SDK_TESTS / "fixtures" / "mini"
FAKE_TRAINER = SDK_TESTS / "fixtures" / "fake_trainer.py"


@dataclass
class Stack:
    home: Path
    profile: Profile
    kind: str


def _profile(home: Path, kms_url: str, cs_url: str, cert: str, cs_cert: str | None = None) -> Profile:
    profile = Profile(home=home).with_overrides(kms_url=kms_url, kms_cert=cert, cs_url=cs_url, cs_cert=cs_cert or cert)
    profile.save()
    return profile


@pytest.fixture(scope="session")
def mock_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    dc = MockDC()
    kms_url, cs_url, cert = dc.serve()
    home = tmp_path_factory.mktemp("mock-home")
    profile = _profile(home, kms_url, cs_url, str(cert))
    stack.configure_dev_governance(
        profile, [profile.kms_client(), profile.cs_client()], profile.kms_client(), profile.cs_client()
    )
    yield Stack(home, profile, "mock")
    dc.stop()


def _sandbox_available() -> bool:
    if os.environ.get("DEPA_E2E_SANDBOX") == "0" or shutil.which("docker") is None:
        return False
    if not all((stack.default_repo(n) / "scripts" / "sandbox-up.sh").exists() for n in ("kms", "cs")):
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


@pytest.fixture(scope="session")
def sandbox_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    if not _sandbox_available():
        pytest.skip("KMS/CS sandboxes not available")
    home = tmp_path_factory.mktemp("sandbox-home")
    kms_port = int(os.environ.get("DEPA_E2E_KMS_PORT", "18160"))
    cs_port = int(os.environ.get("DEPA_E2E_CS_PORT", "18161"))
    try:
        stack.up(home, kms_port=kms_port, cs_port=cs_port)
        yield Stack(home, Profile.load(home), "sandbox")
    finally:
        if (home / "dev-stack.json").exists():
            stack.down(home, purge=True)


def copy_fixture(tmp: Path) -> tuple[Path, dict[str, str]]:
    scenario = tmp / "mini"
    shutil.copytree(FIXTURE, scenario)
    storage = tmp / "storage"
    storage.mkdir()
    return scenario, {"MINI_STORAGE_URI": storage.as_uri() + "/"}


def trainer_cmd() -> str:
    return f"{sys.executable} {FAKE_TRAINER}"
