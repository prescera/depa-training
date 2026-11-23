import base64
from datetime import UTC, datetime
from urllib.parse import parse_qs

import pytest

from broker.sas import SAS_VERSION, DelegationKey, container_sas

KEY = DelegationKey(
    signed_oid="6e9f4b7c-0000-4000-8000-000000000001",
    signed_tid="72f988bf-0000-4000-8000-000000000002",
    signed_start="2027-01-15T09:55:00Z",
    signed_expiry="2027-01-15T16:00:00Z",
    signed_service="b",
    signed_version=SAS_VERSION,
    value=base64.b64encode(bytes(range(32))).decode(),
)
START = datetime(2027, 1, 15, 9, 55, tzinfo=UTC)
EXPIRY = datetime(2027, 1, 15, 10, 5, tzinfo=UTC)


@pytest.mark.parametrize(("access", "perms"), [("read", "rl"), ("write", "cw")])
def test_matches_azure_sdk(access, perms):
    blob = pytest.importorskip("azure.storage.blob")
    from azure.storage.blob._shared.constants import X_MS_VERSION

    assert X_MS_VERSION == SAS_VERSION, "re-check the string-to-sign when bumping SAS_VERSION"
    udk = blob.UserDelegationKey()
    udk.signed_oid, udk.signed_tid = KEY.signed_oid, KEY.signed_tid
    udk.signed_start, udk.signed_expiry = KEY.signed_start, KEY.signed_expiry
    udk.signed_service, udk.signed_version, udk.value = KEY.signed_service, KEY.signed_version, KEY.value
    expected = blob.generate_container_sas(
        "bankadata", "credit-applications", user_delegation_key=udk, permission=perms,
        start=START, expiry=EXPIRY, protocol="https",
    )  # fmt: skip
    ours = container_sas("bankadata", "credit-applications", access, START, EXPIRY, KEY)
    assert parse_qs(ours) == parse_qs(expected)


def test_https_only_and_container_scope():
    q = dict(p.split("=", 1) for p in container_sas("a", "c", "read", START, EXPIRY, KEY).split("&"))
    assert q["spr"] == "https" and q["sr"] == "c" and q["sp"] == "rl" and q["sv"] == SAS_VERSION
