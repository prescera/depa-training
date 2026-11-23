"""HTTPS, GCS, Azure Blob connectors and GCP / Azure federation against respx fakes."""

from __future__ import annotations

import io
import os
import re
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from depa.errors import StorageError
from depa.storage import _common
from depa.storage.factory import ccr_connector
from depa.storage.gcs import GcsConnector

GCP_AUD = "//iam.googleapis.com/projects/123456/locations/global/workloadIdentityPools/depa-pool/providers/depa-kms"
TENANT = "contoso.onmicrosoft.com"
CLIENT_ID = "8f0f6b4e-6a5d-4a52-9a43-3a3f2b9d1c11"
OIDC = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJkYXRhc2V0OmJhbmstYS9jcmVkaXQifQ.c2ln"


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_common, "BACKOFF_S", 0.0)


def _form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


# ---------------------------------------------------------------------------------------------
# https


@respx.mock
def test_https_read_retry_and_read_only() -> None:
    route = respx.get("https://data.example.org/d/objects/a%20b.csv.dedf").mock(
        side_effect=[httpx.Response(503), httpx.Response(502), httpx.Response(200, content=b"payload")]
    )
    conn = ccr_connector(
        {"connector": "https", "uri": "https://data.example.org/d/"},
        None,
        lambda a: "",
        allow_file=False,
        session_name="r",
    )
    with conn.open_read("objects/a b.csv.dedf") as fh:
        assert fh.read() == b"payload"
    assert route.call_count == 3
    with pytest.raises(StorageError) as exc:
        conn.upload("x", io.BytesIO(b"x"), 1)
    assert exc.value.code == "read_only"


@respx.mock
def test_https_not_found_and_retry_exhaustion() -> None:
    respx.get("https://data.example.org/d/missing").mock(return_value=httpx.Response(404))
    respx.get("https://data.example.org/d/flaky").mock(return_value=httpx.Response(503))
    conn = ccr_connector(
        {"connector": "https", "uri": "https://data.example.org/d/"},
        None,
        lambda a: "",
        allow_file=False,
        session_name="r",
    )
    with pytest.raises(StorageError) as exc:
        conn.read_bytes("missing", 10)
    assert exc.value.code == "not_found"
    with pytest.raises(StorageError):
        conn.read_bytes("flaky", 10)
    assert respx.calls.call_count == 1 + _common.MAX_ATTEMPTS


# ---------------------------------------------------------------------------------------------
# GCS + GCP STS


@respx.mock
def test_gcs_federated_read_write() -> None:
    sts = respx.post("https://sts.googleapis.com/v1/token").mock(
        return_value=httpx.Response(
            200, json={"access_token": "gcs-access", "token_type": "Bearer", "expires_in": 3599}
        )
    )
    download = respx.get(
        "https://storage.googleapis.com/storage/v1/b/bkt/o/ds%2Fv1%2Fmanifest.json", params={"alt": "media"}
    ).mock(return_value=httpx.Response(200, content=b"{}"))
    upload = respx.post("https://storage.googleapis.com/upload/storage/v1/b/bkt/o").mock(
        return_value=httpx.Response(200, json={})
    )
    audiences: list[str] = []

    def token_source(aud: str) -> str:
        audiences.append(aud)
        return OIDC

    conn = ccr_connector(
        {"connector": "gcs", "uri": "gs://bkt/ds/v1/"},
        {"gcp": {"audience": GCP_AUD}},
        token_source,
        allow_file=False,
        session_name="r",
        write=True,
    )
    assert audiences == ["https:" + GCP_AUD]
    form = _form(sts.calls.last.request)
    assert form == {
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
        "subject_token": OIDC,
        "audience": GCP_AUD,
        "scope": "https://www.googleapis.com/auth/devstorage.read_write",
    }
    assert conn.read_bytes("manifest.json", 10) == b"{}"
    assert download.calls.last.request.headers["authorization"] == "Bearer gcs-access"
    conn.upload("objects/x.dedf", io.BytesIO(b"abc"), 3)
    req = upload.calls.last.request
    assert req.url.params["uploadType"] == "media" and req.url.params["name"] == "ds/v1/objects/x.dedf"
    assert req.content == b"abc"
    assert "gcs-access" not in repr(conn)


@respx.mock
def test_gcs_read_scope_by_default() -> None:
    sts = respx.post("https://sts.googleapis.com/v1/token").mock(
        return_value=httpx.Response(200, json={"access_token": "t"})
    )
    ccr_connector(
        {"connector": "gcs", "uri": "gs://bkt/ds/"},
        {"gcp": {"audience": GCP_AUD}},
        lambda a: OIDC,
        allow_file=False,
        session_name="r",
    )
    assert _form(sts.calls.last.request)["scope"].endswith("devstorage.read_only")


@respx.mock
def test_gcp_sts_failure_is_storage_error() -> None:
    respx.post("https://sts.googleapis.com/v1/token").mock(return_value=httpx.Response(400, json={"error": "x"}))
    with pytest.raises(StorageError):
        ccr_connector(
            {"connector": "gcs", "uri": "gs://bkt/ds/"},
            {"gcp": {"audience": GCP_AUD}},
            lambda a: OIDC,
            allow_file=False,
            session_name="r",
        )


@respx.mock
def test_gcs_resumable_upload() -> None:
    chunk = 256 << 10
    data = os.urandom(chunk * 2 + 1000)
    session = "https://storage.googleapis.com/upload/storage/v1/b/bkt/o?uploadType=resumable&upload_id=abc"
    init = respx.post(
        "https://storage.googleapis.com/upload/storage/v1/b/bkt/o", params={"uploadType": "resumable"}
    ).mock(return_value=httpx.Response(200, headers={"Location": session}))
    received: list[tuple[str, bytes]] = []

    def put(request: httpx.Request) -> httpx.Response:
        rng = request.headers["content-range"]
        received.append((rng, request.content))
        assert "authorization" not in request.headers
        end = int(re.match(r"bytes \d+-(\d+)/", rng).group(1))  # type: ignore[union-attr]
        if end == len(data) - 1:
            return httpx.Response(200, json={})
        return httpx.Response(308, headers={"Range": f"bytes=0-{end}"})

    respx.put(session).mock(side_effect=put)
    conn = GcsConnector(bucket="bkt", prefix="p/", token="tok", chunk_size=chunk)
    conn.upload("big.dedf", io.BytesIO(data), len(data))
    assert init.calls.last.request.headers["x-upload-content-length"] == str(len(data))
    assert [r for r, _ in received] == [
        f"bytes 0-{chunk - 1}/{len(data)}",
        f"bytes {chunk}-{2 * chunk - 1}/{len(data)}",
        f"bytes {2 * chunk}-{len(data) - 1}/{len(data)}",
    ]
    assert b"".join(c for _, c in received) == data


@respx.mock
def test_gcs_rejects_foreign_session_uri() -> None:
    respx.post("https://storage.googleapis.com/upload/storage/v1/b/bkt/o").mock(
        return_value=httpx.Response(200, headers={"Location": "https://evil.example/upload"})
    )
    conn = GcsConnector(bucket="bkt", prefix="", token="tok", chunk_size=256 << 10)
    with pytest.raises(StorageError):
        conn.upload("big", io.BytesIO(b"x" * (300 << 10)), 300 << 10)


# ---------------------------------------------------------------------------------------------
# Azure Blob + Entra client assertion


@respx.mock
def test_azure_federated_read_and_block_upload() -> None:
    aad = respx.post(f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token").mock(
        return_value=httpx.Response(200, json={"access_token": "az-access", "expires_in": 3599})
    )
    base = "https://acct01.blob.core.windows.net/out/c1/r1/model/objects/w.dedf"
    get = respx.get("https://acct01.blob.core.windows.net/out/c1/r1/model/manifest.json").mock(
        return_value=httpx.Response(200, content=b"{}")
    )
    puts: list[httpx.Request] = []

    def put(request: httpx.Request) -> httpx.Response:
        puts.append(request)
        return httpx.Response(201)

    respx.put(url__startswith=base).mock(side_effect=put)
    audiences: list[str] = []

    def token_source(aud: str) -> str:
        audiences.append(aud)
        return OIDC

    conn = ccr_connector(
        {"connector": "azure-blob", "uri": "https://acct01.blob.core.windows.net/out/c1/r1/model/"},
        {"azure": {"tenant_id": TENANT, "client_id": CLIENT_ID}},
        token_source,
        allow_file=False,
        session_name="r",
        write=True,
    )
    assert audiences == ["api://AzureADTokenExchange"]
    assert _form(aad.calls.last.request) == {
        "client_id": CLIENT_ID,
        "scope": "https://storage.azure.com/.default",
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": OIDC,
    }
    assert conn.read_bytes("manifest.json", 10) == b"{}"
    hdrs = get.calls.last.request.headers
    assert hdrs["authorization"] == "Bearer az-access" and hdrs["x-ms-version"]

    conn._block_size = 1024  # type: ignore[attr-defined]  # exercise Put Block + Put Block List
    data = os.urandom(2500)
    conn.upload("objects/w.dedf", io.BytesIO(data), len(data))
    blocks = [r for r in puts if r.url.params.get("comp") == "block"]
    lists = [r for r in puts if r.url.params.get("comp") == "blocklist"]
    assert len(blocks) == 3 and len(lists) == 1
    assert b"".join(r.content for r in blocks) == data
    ids = [r.url.params["blockid"] for r in blocks]
    assert len({len(i) for i in ids}) == 1
    assert all(f"<Latest>{i}</Latest>".encode() in lists[0].content for i in ids)

    puts.clear()
    conn.upload("objects/w.dedf", io.BytesIO(b"small"), 5)
    assert len(puts) == 1 and puts[0].headers["x-ms-blob-type"] == "BlockBlob"
    assert "az-access" not in repr(conn)


@respx.mock
def test_azure_token_response_without_access_token() -> None:
    respx.post(f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token").mock(
        return_value=httpx.Response(200, json={"token_type": "Bearer"})
    )
    with pytest.raises(StorageError):
        ccr_connector(
            {"connector": "azure-blob", "uri": "https://acct01.blob.core.windows.net/out/"},
            {"azure": {"tenant_id": TENANT, "client_id": CLIENT_ID}},
            lambda a: OIDC,
            allow_file=False,
            session_name="r",
        )


@respx.mock
def test_token_response_size_capped() -> None:
    respx.post(f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token").mock(
        return_value=httpx.Response(200, content=b"{" + b" " * (70 << 10) + b"}")
    )
    with pytest.raises(StorageError) as exc:
        ccr_connector(
            {"connector": "azure-blob", "uri": "https://acct01.blob.core.windows.net/out/"},
            {"azure": {"tenant_id": TENANT, "client_id": CLIENT_ID}},
            lambda a: OIDC,
            allow_file=False,
            session_name="r",
        )
    assert exc.value.code == "too_large"


ES256_OIDC = "eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJkYXRhc2V0OmJhbmstYS94In0.sig"
BROKER = "https://broker.tdp.example/api"
AZ_LOC = {"connector": "azure-blob", "uri": "https://acct01.blob.core.windows.net/data/bank-a/loans/"}
AZ_FED = {"azure": {"broker_url": BROKER, "audience": "api://depa-broker-bank-a"}}


@respx.mock
def test_azure_sas_broker_path() -> None:
    broker = respx.post(f"{BROKER}/sas").mock(
        return_value=httpx.Response(
            200,
            json={
                "sas_url": "https://acct01.blob.core.windows.net/data?sv=2023-11-03&sr=c&sp=rl&spr=https&sig=abc%3D",
                "prefix": "bank-a/loans/",
                "expires_at": "2026-11-23T10:00:00Z",
            },
        )
    )
    get = respx.get(url__startswith="https://acct01.blob.core.windows.net/data/bank-a/loans/manifest.json").mock(
        return_value=httpx.Response(200, content=b"{}")
    )
    audiences: list[str] = []

    def source(aud: str) -> str:
        audiences.append(aud)
        return ES256_OIDC

    conn = ccr_connector(AZ_LOC, AZ_FED, source, allow_file=False, session_name="r")
    assert audiences == ["api://depa-broker-bank-a"]
    assert broker.calls.last.request.headers["authorization"] == f"Bearer {ES256_OIDC}"
    assert conn.read_bytes("manifest.json", 10) == b"{}"
    req = get.calls.last.request
    assert "authorization" not in req.headers and req.url.params["sig"] == "abc="
    assert "sig" not in conn.uri_for("manifest.json") and "abc" not in repr(conn)


@pytest.mark.parametrize(
    "body",
    [
        {
            "sas_url": "https://evil.blob.core.windows.net/data?sig=x",
            "prefix": "",
            "expires_at": "2026-11-23T10:00:00Z",
        },
        {
            "sas_url": "https://acct01.blob.core.windows.net/other?sig=x",
            "prefix": "",
            "expires_at": "2026-11-23T10:00:00Z",
        },
        {
            "sas_url": "https://acct01.blob.core.windows.net/data?sig=x",
            "prefix": "bank-b/",
            "expires_at": "2026-11-23T10:00:00Z",
        },
        {"sas_url": "https://acct01.blob.core.windows.net/data?sig=x&spr=https,http", "prefix": "", "expires_at": "x"},
        {
            "sas_url": "https://acct01.blob.core.windows.net/data?sv=1",
            "prefix": "",
            "expires_at": "2026-11-23T10:00:00Z",
        },
        {"prefix": "", "expires_at": "2026-11-23T10:00:00Z"},
    ],
)
@respx.mock
def test_azure_sas_broker_rejects_bad_grants(body: dict) -> None:
    respx.post(f"{BROKER}/sas").mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(StorageError):
        ccr_connector(AZ_LOC, AZ_FED, lambda aud: ES256_OIDC, allow_file=False, session_name="r")


def test_entra_path_refuses_es256_tokens_with_guidance() -> None:
    with pytest.raises(StorageError, match="RS256") as e:
        ccr_connector(
            AZ_LOC,
            {"azure": {"tenant_id": TENANT, "client_id": CLIENT_ID}},
            lambda aud: ES256_OIDC,
            allow_file=False,
            session_name="r",
        )
    assert e.value.code == "federation_unsupported"


GCP_FED_SA = {
    "gcp": {
        "audience": "//iam.googleapis.com/projects/123/locations/global/workloadIdentityPools/pool1/providers/prov1",
        "service_account": "depa-reader@tdp-proj.iam.gserviceaccount.com",
    }
}


@respx.mock
def test_gcs_federation_with_service_account_impersonation() -> None:
    sts = respx.post("https://sts.googleapis.com/v1/token").mock(
        return_value=httpx.Response(200, json={"access_token": "federated"})
    )
    iam = respx.post(
        "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
        "depa-reader@tdp-proj.iam.gserviceaccount.com:generateAccessToken"
    ).mock(return_value=httpx.Response(200, json={"accessToken": "sa-token", "expireTime": "x"}))
    get = respx.get(url__startswith="https://storage.googleapis.com/storage/v1/b/bkt/o/").mock(
        return_value=httpx.Response(200, content=b"{}")
    )
    conn = ccr_connector(
        {"connector": "gcs", "uri": "gs://bkt/data/"}, GCP_FED_SA, lambda aud: OIDC, allow_file=False, session_name="r"
    )
    assert _form(sts.calls.last.request)["scope"] == "https://www.googleapis.com/auth/cloud-platform"
    assert iam.calls.last.request.headers["authorization"] == "Bearer federated"
    assert b"devstorage.read_only" in iam.calls.last.request.content
    assert conn.read_bytes("manifest.json", 10) == b"{}"
    assert get.calls.last.request.headers["authorization"] == "Bearer sa-token"
