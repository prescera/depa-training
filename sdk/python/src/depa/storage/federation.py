"""Exchange a KMS-issued OIDC token for GCP / Azure storage access tokens (spec §10).

The AWS exchange (``AssumeRoleWithWebIdentity``) lives in ``depa.storage.s3`` so boto3 stays optional.

These functions are the ONLY credential source on the CCR path: no ambient credentials
(environment variables, config files, instance metadata) are ever consulted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from urllib.parse import parse_qs, quote, urlsplit

import httpx

from depa.core.encoding import b64url_decode, loads_object
from depa.core.timeutil import parse_rfc3339
from depa.errors import StorageError, ValidationError
from depa.storage._common import http_client, raise_for_status, read_capped, send

GCP_STS_URL = "https://sts.googleapis.com/v1/token"
AZURE_AUTHORITY = "https://login.microsoftonline.com"
AZURE_STORAGE_SCOPE = "https://storage.azure.com/.default"
GCP_IAM_CREDENTIALS_URL = "https://iamcredentials.googleapis.com/v1"
CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
GCS_READ_SCOPE = "https://www.googleapis.com/auth/devstorage.read_only"
GCS_WRITE_SCOPE = "https://www.googleapis.com/auth/devstorage.read_write"
MAX_TOKEN_RESPONSE = 64 << 10


def _token_from_response(response: httpx.Response, what: str) -> str:
    raise_for_status(response, what)
    try:
        body = loads_object(read_capped(response, MAX_TOKEN_RESPONSE, what))
    except ValidationError as exc:
        raise StorageError(f"{what}: malformed token response", code="federation") from exc
    token = body.get("access_token")
    if not isinstance(token, str) or not token:
        raise StorageError(f"{what}: response has no access_token", code="federation")
    return token


def gcp_sts_exchange(
    subject_token: str,
    *,
    audience: str,
    write: bool,
    client: httpx.Client | None = None,
    service_account: str | None = None,
) -> str:
    """GCP STS token exchange for a Workload Identity Federation provider (``//iam.googleapis.com/...``).

    With ``service_account`` the federated token (scope ``cloud-platform``) is then exchanged for a
    short-lived access token of that service account via IAM Credentials ``generateAccessToken``.
    """
    what = "GCP STS token exchange"
    form = {
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
        "subject_token": subject_token,
        "audience": audience,
        "scope": CLOUD_PLATFORM_SCOPE if service_account else (GCS_WRITE_SCOPE if write else GCS_READ_SCOPE),
    }
    http = client or http_client(30.0)
    try:
        response = send(http, "POST", GCP_STS_URL, what=what, data=form, stream=True)
        federated = _token_from_response(response, what)
        if service_account is None:
            return federated
        what = "GCP IAM Credentials generateAccessToken"
        account = quote(service_account, safe="@.")
        url = f"{GCP_IAM_CREDENTIALS_URL}/projects/-/serviceAccounts/{account}:generateAccessToken"
        body = {"scope": [GCS_WRITE_SCOPE if write else GCS_READ_SCOPE], "lifetime": "3600s"}
        response = send(
            http,
            "POST",
            url,
            what=what,
            content=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {federated}", "Content-Type": "application/json"},
            stream=True,
        )
        raise_for_status(response, what)
        try:
            token = loads_object(read_capped(response, MAX_TOKEN_RESPONSE, what)).get("accessToken")
        except ValidationError as exc:
            raise StorageError(f"{what}: malformed response", code="federation") from exc
        if not isinstance(token, str) or not token:
            raise StorageError(f"{what}: response has no accessToken", code="federation")
        return token
    finally:
        if client is None:
            http.close()


@dataclass(frozen=True)
class SasGrant:
    """A user-delegation SAS from the TDP's broker (spec §10.1)."""

    sas: str = field(repr=False)
    prefix: str
    expires_at: str


def azure_sas_broker(
    oidc_token: str,
    *,
    broker_url: str,
    account_host: str,
    container: str,
    prefix: str,
    client: httpx.Client | None = None,
) -> SasGrant:
    """``POST <broker_url>/sas`` with the KMS token; validate the SAS scope against the location.

    Response ``{"sas_url": "https://<acct>.blob.core.windows.net/<container>?<sas>", "prefix", "expires_at"}``.
    The SAS must be for exactly this account and container, HTTPS-only, and its prefix must
    contain the dataset/output prefix.
    """
    what = "Azure SAS broker"
    http = client or http_client(30.0)
    try:
        response = send(
            http,
            "POST",
            broker_url.rstrip("/") + "/sas",
            what=what,
            headers={"Authorization": f"Bearer {oidc_token}"},
            stream=True,
        )
        raise_for_status(response, what)
        try:
            body = loads_object(read_capped(response, MAX_TOKEN_RESPONSE, what))
        except ValidationError as exc:
            raise StorageError(f"{what}: malformed response", code="federation") from exc
    finally:
        if client is None:
            http.close()
    sas_url, granted_prefix, expires_at = body.get("sas_url"), body.get("prefix", ""), body.get("expires_at")
    if not isinstance(sas_url, str) or not isinstance(granted_prefix, str) or not isinstance(expires_at, str):
        raise StorageError(f"{what}: response lacks sas_url/prefix/expires_at", code="federation")
    parts = urlsplit(sas_url)
    if parts.scheme != "https" or parts.hostname != account_host or parts.path.strip("/") != container:
        raise StorageError(f"{what}: SAS is not for this storage account/container", code="federation")
    query = parse_qs(parts.query)
    if not query.get("sig") or query.get("spr", ["https"])[0] != "https":
        raise StorageError(f"{what}: SAS is unsigned or allows plain HTTP", code="federation")
    if not prefix.startswith(granted_prefix.lstrip("/")):
        raise StorageError(f"{what}: SAS prefix does not cover the location", code="federation")
    parse_rfc3339(expires_at)
    return SasGrant(sas=parts.query, prefix=granted_prefix, expires_at=expires_at)


def jwt_alg(token: str) -> str | None:
    try:
        header = loads_object(b64url_decode(token.split(".", 1)[0]))
    except (ValidationError, IndexError):
        return None
    alg = header.get("alg")
    return alg if isinstance(alg, str) else None


def azure_client_assertion(
    assertion: str, *, tenant_id: str, client_id: str, client: httpx.Client | None = None
) -> str:
    """Entra ID client-credentials grant with a federated client assertion (the KMS token)."""
    what = "Azure client-assertion token request"
    if jwt_alg(assertion) != "RS256":
        raise StorageError(
            "Entra ID federated credentials accept only RS256 assertions but the KMS issues "
            f"{jwt_alg(assertion)} tokens; use the SAS broker federation (azure: {{broker_url, audience}}, spec §10.1)",
            code="federation_unsupported",
        )
    form = {
        "client_id": client_id,
        "scope": AZURE_STORAGE_SCOPE,
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": assertion,
    }
    url = f"{AZURE_AUTHORITY}/{tenant_id}/oauth2/v2.0/token"
    http = client or http_client(30.0)
    try:
        response = send(http, "POST", url, what=what, data=form, stream=True)
        return _token_from_response(response, what)
    finally:
        if client is None:
            http.close()
