import base64
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import AUDIENCE, ISSUER, NOW, Signer

from broker.handler import Broker, Config, Grant
from broker.sas import DelegationKey, format_time

GRANTS = {
    "dataset:bank-a/credit-applications": Grant(container="credit-applications", prefix="", access="read"),
    "output:fintech-x": Grant(container="outputs", prefix="", access="write"),
}


def config(signer: Signer, **kw) -> Config:
    base = {
        "issuer": ISSUER, "audience": AUDIENCE, "jwks": {"keys": [signer.jwk()]}, "jwks_refresh": False,
        "account": "bankadata", "blob_endpoint": "https://bankadata.blob.core.windows.net",
        "identity_client_id": "cid", "grants": GRANTS, "sas_max_ttl_s": 900,
    }  # fmt: skip
    return Config(**(base | kw))


def key_provider(now: datetime, until: datetime) -> DelegationKey:
    assert until > now
    return DelegationKey(
        "oid", "tid", format_time(now - timedelta(minutes=5)), format_time(until + timedelta(hours=1)),
        "b", "2026-10-06", base64.b64encode(b"k" * 32).decode(),
    )  # fmt: skip


def test_read_grant(signer):
    status, body = Broker(config(signer), key_provider).handle(f"Bearer {signer.token()}", NOW)
    assert status == 200
    assert set(body) == {"sas_url", "prefix", "expires_at"}
    url = urlsplit(body["sas_url"])
    q = parse_qs(url.query)
    assert url.scheme == "https" and url.path == "/credit-applications"
    assert q["sp"] == ["rl"] and q["spr"] == ["https"] and q["sr"] == ["c"]
    # expires with the token (exp = NOW + 290), not the 900 s cap
    assert body["expires_at"] == format_time(datetime.fromtimestamp(NOW + 290, UTC))


def test_write_grant_and_ttl_cap(signer):
    token = signer.token(sub="output:fintech-x", exp=NOW + 3000, nbf=NOW - 10)
    status, body = Broker(config(signer), key_provider).handle(f"Bearer {token}", NOW)
    assert status == 200
    assert parse_qs(urlsplit(body["sas_url"]).query)["sp"] == ["cw"]
    assert body["expires_at"] == format_time(datetime.fromtimestamp(NOW + 900, UTC))


@pytest.mark.parametrize("auth", [None, "", "Bearer ", "Basic abc", "bearer"])
def test_missing_bearer(signer, auth):
    assert Broker(config(signer), key_provider).handle(auth, NOW) == (401, {"error": "unauthorized"})


def test_invalid_token_is_401_without_detail(signer):
    status, body = Broker(config(signer), key_provider).handle(f"Bearer {signer.token(iss='x')}", NOW)
    assert (status, body) == (401, {"error": "unauthorized"})


def test_unconfigured_subject_is_403(signer):
    token = signer.token(sub="dataset:bank-a/other")
    assert Broker(config(signer), key_provider).handle(f"Bearer {token}", NOW) == (403, {"error": "forbidden"})


def test_unknown_kid_without_refresh_is_401(signer):
    rotated = Signer(kid="k2")
    fetched = []
    broker = Broker(config(signer), key_provider, lambda iss: fetched.append(iss) or {"keys": [rotated.jwk()]})
    assert broker.handle(f"Bearer {rotated.token()}", NOW)[0] == 401
    assert fetched == []


def test_unknown_kid_refreshes_from_issuer_once(signer):
    rotated = Signer(kid="k2")
    fetched = []
    broker = Broker(
        config(signer, jwks_refresh=True), key_provider, lambda iss: fetched.append(iss) or {"keys": [rotated.jwk()]}
    )
    assert broker.handle(f"Bearer {rotated.token()}", NOW)[0] == 200
    assert fetched == [ISSUER]
    assert broker.handle(f"Bearer {Signer(kid='k3').token()}", NOW + 1)[0] == 401  # rate limited
    assert fetched == [ISSUER]


def test_config_from_env(signer):
    import json

    env = {
        "BROKER_ISSUER": ISSUER, "BROKER_AUDIENCE": AUDIENCE, "BROKER_JWKS": json.dumps({"keys": [signer.jwk()]}),
        "BROKER_ACCOUNT_NAME": "bankadata", "BROKER_BLOB_ENDPOINT": "https://bankadata.blob.core.windows.net/",
        "BROKER_IDENTITY_CLIENT_ID": "cid",
        "BROKER_GRANTS": json.dumps({"output:fintech-x": {"container": "outputs", "access": "write"}}),
    }  # fmt: skip
    cfg = Config.from_env(env)
    assert cfg.blob_endpoint.endswith(".net") and cfg.grants["output:fintech-x"].access == "write"
    with pytest.raises(ValueError):
        Config.from_env(env | {"BROKER_GRANTS": json.dumps({"x": {"container": "c", "access": "delete"}})})
