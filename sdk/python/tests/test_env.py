"""depa-env.yaml schema and loader."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from depa.env import env_problems, load_env
from depa.errors import ValidationError
from test_clients_receipts_config import _cert


def _pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    return (
        _cert("svc", ec.generate_private_key(ec.SECP256R1()), ca=True).public_bytes(serialization.Encoding.PEM).decode()
    )


def _doc(tmp: Path) -> dict:
    (tmp / "kms.pem").write_text(_pem())
    return {
        "collaborative": {
            "kms": {
                "url": "https://kms.example/app",
                "service_cert": "kms.pem",
                "oidc_issuer": "https://oidc.example/app/oidc",
            },
            "cs": {"url": "https://cs.example/app", "service_cert": _pem()},
        },
        "parties": [
            {"party_id": "bank-a", "roles": ["tdp"], "key_dir": "keys/bank-a"},
            {"party_id": "tdc", "roles": ["tdc"], "key_dir": "keys/tdc"},
        ],
        "storage": {
            "bank-a": {
                "connector": "azure-blob",
                "uri": "https://acct01.blob.core.windows.net/data/",
                "federation": {"azure": {"broker_url": "https://broker.example/api", "audience": "api://depa-broker"}},
            }
        },
        "outputs": {
            "tdc": {
                "connector": "s3",
                "uri": "s3://out-bucket/models/",
                "region": "ap-south-1",
                "federation": {"aws": {"role_arn": "arn:aws:iam::123456789012:role/depa-out"}},
            }
        },
        "compute": {"aks": {"subscription_id": "s", "resource_group": "rg", "cluster_name": "c", "namespace": "depa"}},
        "registry": {"login_server": "example.azurecr.io"},
    }


def _write(tmp: Path, doc: dict) -> Path:
    path = tmp / "depa-env.yaml"
    path.write_text(yaml.safe_dump(doc))
    return path


def test_valid_env_and_profile(tmp_path: Path) -> None:
    path = _write(tmp_path, _doc(tmp_path))
    errors, warnings = env_problems(path)
    assert errors == [] and any("no keys" in w for w in warnings)
    env = load_env(path)
    profile = env.profile(tmp_path / "home", "bank-a")
    assert profile.cs.url == "https://cs.example/app"
    assert Path(str(profile.cs.cert)).read_text().startswith("-----BEGIN CERTIFICATE-----")  # inline PEM materialised
    assert profile.keys_dir("bank-a") == (tmp_path / "keys" / "bank-a").resolve()
    assert env.storage("bank-a")["federation"]["azure"]["audience"] == "api://depa-broker"


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda d: d["collaborative"]["kms"].update(url="http://kms"), "collaborative/kms/url"),
        (lambda d: d["storage"].update(mallory={"connector": "file", "uri": "file:///x/"}), "not a party"),
        (lambda d: d["outputs"].update({"bank-a": d["outputs"]["tdc"]}), "lacks role tdc"),
        (lambda d: d["outputs"]["tdc"].pop("federation"), "requires federation"),
        (lambda d: d["collaborative"]["kms"].update(service_cert="missing.pem"), "not readable"),
        (lambda d: d.update(extra=1), "Additional properties"),
    ],
)
def test_invalid_env(tmp_path: Path, mutate, fragment: str) -> None:
    doc = _doc(tmp_path)
    mutate(doc)
    path = _write(tmp_path, doc)
    errors, _ = env_problems(path)
    assert any(fragment in e for e in errors), errors
    with pytest.raises(ValidationError):
        load_env(path)
