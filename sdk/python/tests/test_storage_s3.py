from __future__ import annotations

import io
import os
from collections.abc import Iterator

import boto3
import pytest
from botocore.config import Config
from moto import mock_aws

from depa.errors import StorageError
from depa.storage.factory import ccr_connector, party_connector

BUCKET = "bank-a-data"
LOCATION = {"connector": "s3", "uri": f"s3://{BUCKET}/credit/2026-11-01/", "region": "us-east-1"}
FEDERATION = {"aws": {"role_arn": "arn:aws:iam::123456789012:role/depa-reader"}}


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Ambient credentials that MUST NOT be used on the CCR path.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AMBIENTAMBIENTAMBIENT")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ambient-secret")
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        monkeypatch.setenv("AWS_ENDPOINT_URL", "https://attacker.example")
        yield


def test_ccr_s3_federated_roundtrip(aws: None) -> None:
    audiences: list[str] = []

    def token_source(audience: str) -> str:
        audiences.append(audience)
        return "header.payload.signature"

    conn = ccr_connector(LOCATION, FEDERATION, token_source, allow_file=False, session_name="run 1234", write=True)
    assert audiences == ["sts.amazonaws.com"]
    client = conn._client  # type: ignore[attr-defined]
    creds = client._request_signer._credentials
    assert creds.access_key != "AMBIENTAMBIENTAMBIENT"
    assert creds.token  # session credentials from STS
    assert "attacker" not in client.meta.endpoint_url

    small = b"{}"
    conn.upload("manifest.json", io.BytesIO(small), len(small))
    big = os.urandom(11 << 20)  # > 8 MiB part size -> multipart upload
    conn.upload("objects/a.parquet.dedf", io.BytesIO(big), len(big))
    assert conn.read_bytes("manifest.json", 1024) == small
    with conn.open_read("objects/a.parquet.dedf") as body:
        got = b"".join(iter(lambda: body.read(1 << 20), b""))
    assert got == big
    assert conn.uri_for("manifest.json") == f"s3://{BUCKET}/credit/2026-11-01/manifest.json"
    plain = boto3.client("s3", region_name="us-east-1", config=Config(ignore_configured_endpoint_urls=True))
    raw = plain.head_object(Bucket=BUCKET, Key="credit/2026-11-01/objects/a.parquet.dedf")
    assert raw["ContentLength"] == len(big)
    assert raw["ETag"].strip('"').endswith("-2")  # multipart: two 8 MiB-max parts


def test_s3_not_found_and_limits(aws: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_ENDPOINT_URL")  # the party side legitimately honours ambient config
    conn = party_connector(LOCATION)
    with pytest.raises(StorageError) as exc:
        conn.read_bytes("missing.json", 10)
    assert exc.value.code == "not_found"
    conn.upload("x.bin", io.BytesIO(b"0123456789"), 10)
    with pytest.raises(StorageError) as exc:
        conn.read_bytes("x.bin", 9)
    assert exc.value.code == "too_large"
    with pytest.raises(StorageError):
        conn.upload("y.bin", io.BytesIO(b"short"), 10)
