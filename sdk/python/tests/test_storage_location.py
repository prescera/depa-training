from __future__ import annotations

import pytest

from depa.errors import ConfigError, ValidationError
from depa.storage.factory import ccr_connector, federation_audience, party_connector, validate_location

GCP_AUD = "//iam.googleapis.com/projects/123456/locations/global/workloadIdentityPools/depa-pool/providers/depa-kms"
FED = {
    "aws": {"role_arn": "arn:aws:iam::123456789012:role/depa-reader"},
    "gcp": {"audience": GCP_AUD},
    "azure": {"tenant_id": "contoso.onmicrosoft.com", "client_id": "8f0f6b4e-6a5d-4a52-9a43-3a3f2b9d1c11"},
}


@pytest.mark.parametrize(
    "location",
    [
        {"connector": "s3", "uri": "s3://bank-a-data/credit/2026-11-01/", "region": "ap-south-1"},
        {
            "connector": "s3",
            "uri": "s3://bucket/",
            "region": "us-east-1",
            "endpoint": "http://127.0.0.1:18210",
        },
        {
            "connector": "s3",
            "uri": "s3://bucket/x/",
            "region": "us-east-1",
            "endpoint": "https://minio.example",
        },
        {"connector": "gcs", "uri": "gs://my-bucket/p/q/"},
        {"connector": "azure-blob", "uri": "https://acct01.blob.core.windows.net/data/v1/"},
        {"connector": "azure-blob", "uri": "https://acct01.blob.core.windows.net/data/"},
        {"connector": "https", "uri": "https://data.example.org/datasets/a/"},
        {"connector": "https", "uri": "http://127.0.0.1:8080/d/"},
        {"connector": "file", "uri": "file:///tmp/depa/data/"},
    ],
)
def test_valid_locations(location: dict[str, str]) -> None:
    validate_location(location)
    validate_location(location, federation=FED)


@pytest.mark.parametrize(
    "location",
    [
        {"connector": "ftp", "uri": "ftp://x/"},
        {"connector": "s3", "uri": "s3://bucket/prefix/"},  # region missing
        {"connector": "s3", "uri": "s3://bucket/prefix", "region": "us-east-1"},  # no trailing slash
        {"connector": "s3", "uri": "s3://bucket/../x/", "region": "us-east-1"},
        {"connector": "s3", "uri": "s3://bucket//x/", "region": "us-east-1"},
        {"connector": "s3", "uri": "s3://bucket/x/?a=b", "region": "us-east-1"},
        {"connector": "s3", "uri": "s3://user:pw@bucket/x/", "region": "us-east-1"},
        {
            "connector": "s3",
            "uri": "s3://bucket/x/",
            "region": "us-east-1",
            "endpoint": "http://minio.example",
        },
        {"connector": "gcs", "uri": "s3://bucket/x/"},
        {"connector": "gcs", "uri": "gs://bucket/x/", "endpoint": "https://e.example"},
        {"connector": "azure-blob", "uri": "https://acct.blob.example.net/c/"},
        {"connector": "azure-blob", "uri": "https://acct01.blob.core.windows.net/AB/"},
        {"connector": "azure-blob", "uri": "http://acct01.blob.core.windows.net/data/"},
        {"connector": "https", "uri": "http://data.example.org/d/"},
        {"connector": "https", "uri": "https://data.example.org/d/#frag"},
        {"connector": "file", "uri": "file://host/tmp/x/"},
        {"connector": "file", "uri": "file:///tmp/../etc/"},
        {"connector": "file", "uri": "file:///tmp/x/", "extra": 1},
        {"connector": "file", "uri": "file:///tmp/x/", "federation": {}},
    ],
)
def test_invalid_locations(location: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        validate_location(location)


def test_federation_rules() -> None:
    s3 = {"connector": "s3", "uri": "s3://bucket/x/", "region": "us-east-1"}
    with pytest.raises(ValidationError, match=r"requires federation\.aws"):
        validate_location(s3, federation={"gcp": FED["gcp"]})
    with pytest.raises(ValidationError):
        validate_location(s3, federation={"aws": {"role_arn": "not-an-arn"}})
    with pytest.raises(ValidationError):
        validate_location(s3, federation={"aws": {"role_arn": FED["aws"]["role_arn"], "extra": "x"}})
    with pytest.raises(ValidationError):
        validate_location(s3, federation={**FED, "oci": {}})
    with pytest.raises(ValidationError):
        validate_location({"connector": "gcs", "uri": "gs://bkt/x/"}, federation={"gcp": {"audience": "https://evil"}})
    with pytest.raises(ValidationError):
        validate_location(
            {"connector": "azure-blob", "uri": "https://acct01.blob.core.windows.net/data/"},
            federation={"azure": {"tenant_id": "t", "client_id": "not-a-guid"}},
        )
    validate_location(s3, federation={"aws": {"role_arn": "arn:minio:iam:::role/depa-dev"}})


def test_output_location_federation_inline() -> None:
    out = {
        "connector": "azure-blob",
        "uri": "https://acct01.blob.core.windows.net/out/",
        "federation": {"azure": FED["azure"]},
    }
    validate_location(out, for_output=True)
    with pytest.raises(ValidationError):
        validate_location({k: v for k, v in out.items() if k != "federation"}, for_output=True)
    validate_location({"connector": "file", "uri": "file:///tmp/out/"}, for_output=True)


def test_federation_audience() -> None:
    assert federation_audience("s3", FED) == "sts.amazonaws.com"
    assert federation_audience("azure-blob", FED) == "api://AzureADTokenExchange"
    assert federation_audience("gcs", FED) == "https:" + GCP_AUD
    assert federation_audience("https", {}) is None
    assert federation_audience("file", {}) is None


def test_ccr_connector_refuses_file_unless_allowed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    loc = {"connector": "file", "uri": tmp_path.as_uri() + "/"}

    def never(_aud: str) -> str:
        raise AssertionError("no token needed")

    with pytest.raises(ConfigError):
        ccr_connector(loc, None, never, allow_file=False, session_name="run")
    assert ccr_connector(loc, None, never, allow_file=True, session_name="run").connector == "file"


def test_ccr_connector_requires_federation_and_https_read_only() -> None:
    def never(_aud: str) -> str:
        raise AssertionError("no token expected")

    with pytest.raises(ValidationError):
        ccr_connector(
            {"connector": "s3", "uri": "s3://b1b/x/", "region": "us-east-1"},
            None,
            never,
            allow_file=False,
            session_name="r",
        )
    https = {"connector": "https", "uri": "https://data.example.org/d/"}
    with pytest.raises(ConfigError):
        ccr_connector(https, None, never, allow_file=False, session_name="r", write=True)
    assert ccr_connector(https, None, never, allow_file=False, session_name="r").connector == "https"


def test_party_connector_requires_token_for_gcs_and_azure() -> None:
    with pytest.raises(ConfigError):
        party_connector({"connector": "gcs", "uri": "gs://bkt/x/"})
    with pytest.raises(ConfigError):
        party_connector({"connector": "azure-blob", "uri": "https://acct01.blob.core.windows.net/data/"})
    conn = party_connector({"connector": "gcs", "uri": "gs://bkt/x/"}, access_token="tok")
    assert "tok" not in repr(conn)
