"""Cancellation, template placement, encryption progress, mock endpoints and configurable provenance."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

from conftest import World
from depa.contract.authoring import DatasetTemplate
from depa.deploy import cancellable_runner
from depa.deployment import DeployOptions, deploy_scenario, locate_dataset_template
from depa.dev.stack import virtual_release
from depa.env import load_env
from depa.errors import DeployCancelled
from depa.workflows import encrypt_dataset
from test_env import _doc, _write


def test_cancellable_runner_terminates_promptly() -> None:
    cancel = threading.Event()
    runner = cancellable_runner(cancel, poll_s=0.05, grace_s=2)
    threading.Timer(0.3, cancel.set).start()
    start = time.monotonic()
    with pytest.raises(DeployCancelled):
        runner([sys.executable, "-c", "import time; time.sleep(60)"])
    assert time.monotonic() - start < 5
    done = cancellable_runner(threading.Event())([sys.executable, "-c", "print('hi')"])
    assert done.returncode == 0 and done.stdout.strip() == b"hi"


def test_deploy_scenario_honours_cancellation(world: World, tmp_path: Path) -> None:
    cancel = threading.Event()
    cancel.set()
    env = load_env(_write(tmp_path, _doc(tmp_path)))
    with pytest.raises(DeployCancelled):
        deploy_scenario(
            world.scenario, env, world.profile, DeployOptions(platform="local", cancel=cancel), log=lambda m: None
        )


def test_locate_dataset_template(tmp_path: Path) -> None:
    env = load_env(_write(tmp_path, _doc(tmp_path)))
    template = DatasetTemplate(
        descriptor={
            "dataset_id": "bank-a/loans",
            "version": "v1",
            "name": "L",
            "format": "dedf/1",
            "location": {"connector": "file", "uri": "file:///x/"},
            "federation": {},
            "kms": {"url": "https://old/app", "kid": "bank-a/loans#1"},
        },
        policy={"version": 1, "allowed_platforms": ["virtual"], "allowed_tdcs": ["*"]},
        source=None,
    )
    placed = locate_dataset_template(env, template, platform="aci-snp")
    assert placed.descriptor["location"] == {
        "connector": "azure-blob",
        "uri": "https://acct01.blob.core.windows.net/data/loans/v1/",
    }
    assert placed.descriptor["federation"]["azure"]["audience"] == "api://depa-broker"
    assert placed.descriptor["kms"] == {"url": "https://kms.example/app", "kid": "bank-a/loans#1"}
    assert placed.policy is not None
    assert placed.policy["allowed_platforms"] == ["virtual", "aci-snp"]
    assert placed.policy["allowed_tdcs"] == ["tdc"] and placed.policy["allowed_audiences"] == ["api://depa-broker"]


def test_encrypt_dataset_progress(world: World, tmp_path: Path) -> None:
    from depa.contract.authoring import load_dataset_template

    events: list[tuple[str, int, int]] = []
    t = load_dataset_template(world.scenario / "datasets" / "bureau-scores.yaml", world.env)
    encrypt_dataset(
        t,
        kms=world.kms,
        signer=world.profile.signer("bureau"),
        out_dir=tmp_path / "progress-enc",
        segment_size=1024,
        progress=lambda p, done, total: events.append((p, done, total)),
    )
    by_file: dict[str, list[tuple[int, int]]] = {}
    for path, done, total in events:
        by_file.setdefault(path, []).append((done, total))
    assert set(by_file) == {"scores.csv", "nested/empty.txt"}
    scores = by_file["scores.csv"]
    assert scores[0][0] == 0 and scores[-1][0] == scores[-1][1] > 1024 and len(scores) > 2
    assert by_file["nested/empty.txt"] == [(0, 0), (0, 0)]


def test_mock_service_read_endpoints(world: World) -> None:
    kms, cs = world.clients()
    proposals = kms.gov_proposals()
    assert kms.gov_proposal(proposals[0]["proposal_id"])["proposer"] == "dc-member-1"
    constitution = cs.gov_constitution()
    assert constitution["effective_thresholds"]["set_settings"] == "unanimous"
    history = kms.policy_history("bank-a/loans")
    assert history["versions"][0]["version"] == 1 and history["versions"][0]["policy"]["version"] == 1
    listing = cs.list_datasets()
    ids = {d["dataset_id"]: d for d in listing["datasets"]}
    assert ids["bank-a/loans"]["latest_version"] == "2026-11-01" and ids["bureau/scores"]["owner"] == "bureau"


def test_provenance_source_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    assert virtual_release()["provenance"]["source"] == "https://github.com/prescera/depa-training"
    monkeypatch.setenv("DEPA_SOURCE_REPO", "https://example.org/fork")
    assert virtual_release()["provenance"]["source"] == "https://example.org/fork"


def test_scenario_terms_default_to_prescera() -> None:
    root = Path(__file__).resolve().parents[3] / "scenarios"
    for contract in root.glob("*/contract.yaml"):
        text = contract.read_text()
        assert "github.com/iSPIRT" not in text and "github.com/prescera/depa-training" in text
        assert yaml.safe_load(text)  # still valid YAML before substitution
