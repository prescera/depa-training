"""Location validation and connector construction (CCR federation path and party path)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from depa.errors import ConfigError, ValidationError
from depa.storage import CONNECTORS, Connector, TokenSource
from depa.storage._common import check_key, is_loopback
from depa.storage.azure_blob import AzureBlobConnector
from depa.storage.federation import azure_client_assertion, azure_sas_broker, gcp_sts_exchange
from depa.storage.file import FileConnector
from depa.storage.gcs import GcsConnector
from depa.storage.https import HttpsConnector

AWS_AUDIENCE = "sts.amazonaws.com"
AZURE_AUDIENCE = "api://AzureADTokenExchange"

_S3_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_GCS_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$")
_AZ_ACCOUNT_RE = re.compile(r"^[a-z0-9]{3,24}$")
_AZ_CONTAINER_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){2,62}$")
_REGION_RE = re.compile(r"^[a-z0-9-]{2,32}$")
_ROLE_ARN_RE = re.compile(r"^arn:[a-z0-9-]+:iam::\d{0,12}:role/[\w+=,.@/-]{1,512}$")
_GCP_AUDIENCE_RE = re.compile(
    r"^//iam\.googleapis\.com/projects/\d+/locations/global/workloadIdentityPools/[a-z0-9-]{4,32}"
    r"/providers/[a-z0-9-]{4,32}$"
)
_TENANT_RE = re.compile(r"^[A-Za-z0-9.-]{1,100}$")
_FEDERATION_SHAPES: dict[str, frozenset[str]] = {
    "aws": frozenset({"role_arn"}),
    "gcp": frozenset({"audience"}),
    "azure": frozenset({"tenant_id", "client_id"}),
}
_AZURE_BROKER_SHAPE = frozenset({"broker_url", "audience"})
_GCP_IMPERSONATION_SHAPE = frozenset({"audience", "service_account"})
_GCP_SA_RE = re.compile(r"^[a-z][a-z0-9-]{4,61}@[a-z0-9.-]{1,200}\.gserviceaccount\.com$")
_CONNECTOR_FEDERATION = {"s3": "aws", "gcs": "gcp", "azure-blob": "azure"}


@dataclass(frozen=True)
class ParsedLocation:
    connector: str
    uri: str
    host: str  # bucket (s3/gcs), account host (azure-blob), base URL (https), root dir (file)
    container: str  # azure-blob container, else ""
    prefix: str  # key prefix ("" or ending with "/")
    region: str | None
    endpoint: str | None


def _bad(message: str) -> ValidationError:
    return ValidationError(message, code="bad_location")


def _prefix(path: str) -> str:
    """Validate a key prefix taken from a URI path (``""`` or ``a/b/``)."""
    if not path:
        return ""
    if not path.endswith("/"):
        raise _bad("location prefix must end with '/'")
    try:
        check_key(path[:-1])
    except ValidationError as exc:
        raise _bad(f"invalid location prefix {path!r}") from exc
    return path


def _parse(location: Mapping[str, Any]) -> ParsedLocation:
    connector, uri = location.get("connector"), location.get("uri")
    if connector not in CONNECTORS:
        raise _bad(f"unknown connector {connector!r}")
    if not isinstance(uri, str) or not uri:
        raise _bad("location uri is required")
    region = location.get("region")
    if region is not None and (not isinstance(region, str) or not _REGION_RE.fullmatch(region)):
        raise _bad(f"invalid region {region!r}")
    endpoint = location.get("endpoint")
    if endpoint is not None:
        if connector != "s3":
            raise _bad("endpoint is only supported for the s3 connector")
        ep = urlsplit(str(endpoint))
        if ep.scheme not in ("https", "http") or not ep.hostname or ep.path not in ("", "/") or ep.query:
            raise _bad("endpoint must be an https:// origin")
        if ep.scheme == "http" and not is_loopback(ep.hostname):
            raise _bad("plain-http endpoints are only allowed on loopback")
        if ep.username or ep.password:
            raise _bad("endpoint must not embed credentials")
    parts = urlsplit(uri)
    if parts.query or parts.fragment or parts.username or parts.password:
        raise _bad("location uri must not contain credentials, a query or a fragment")
    if not uri.endswith("/"):
        raise _bad("location uri must end with '/'")
    path = parts.path
    if connector == "s3":
        if parts.scheme != "s3" or not parts.hostname or not _S3_BUCKET_RE.fullmatch(parts.netloc):
            raise _bad("s3 uri must be s3://<bucket>/<prefix>/")
        if region is None:
            raise _bad("s3 locations require a region")
        return ParsedLocation("s3", uri, parts.netloc, "", _prefix(path[1:]), region, endpoint)
    if connector == "gcs":
        if parts.scheme != "gs" or not _GCS_BUCKET_RE.fullmatch(parts.netloc):
            raise _bad("gcs uri must be gs://<bucket>/<prefix>/")
        return ParsedLocation("gcs", uri, parts.netloc, "", _prefix(path[1:]), region, None)
    if connector == "azure-blob":
        host = parts.netloc
        account = host.removesuffix(".blob.core.windows.net")
        if parts.scheme != "https" or account == host or not _AZ_ACCOUNT_RE.fullmatch(account):
            raise _bad("azure-blob uri must be https://<account>.blob.core.windows.net/<container>/<prefix>/")
        container, _, rest = path[1:].partition("/")
        if not _AZ_CONTAINER_RE.fullmatch(container):
            raise _bad(f"invalid azure container {container!r}")
        return ParsedLocation("azure-blob", uri, host, container, _prefix(rest), region, None)
    if connector == "https":
        if parts.scheme not in ("https", "http") or not parts.hostname:
            raise _bad("https uri must be https://<host>/<prefix>/")
        if parts.scheme == "http" and not is_loopback(parts.hostname):
            raise _bad("plain-http locations are only allowed on loopback")
        if "%" in path or "\\" in path or "/../" in path or "/./" in path:
            raise _bad("https location path must be plain")
        return ParsedLocation("https", uri, uri, "", "", region, None)
    # file
    if parts.scheme != "file" or parts.netloc not in ("", "localhost"):
        raise _bad("file uri must be file:///<absolute dir>/")
    root = unquote(path)
    if not root.startswith("/") or "\x00" in root or any(p in ("..", ".") for p in root.split("/")):
        raise _bad("file uri must name an absolute directory")
    return ParsedLocation("file", uri, root, "", "", region, None)


def _validate_federation(connector: str, federation: Mapping[str, Any]) -> None:
    if not isinstance(federation, Mapping):
        raise _bad("federation must be an object")
    for name, block in federation.items():
        shape = _FEDERATION_SHAPES.get(name)
        if shape is None:
            raise _bad(f"unknown federation block {name!r}")
        shapes = {"azure": (shape, _AZURE_BROKER_SHAPE), "gcp": (shape, _GCP_IMPERSONATION_SHAPE)}.get(name, (shape,))
        well_formed = isinstance(block, Mapping) and frozenset(block) in shapes
        if not well_formed or not all(isinstance(v, str) for v in block.values()):
            raise _bad(f"federation.{name} must contain exactly {' or '.join(str(sorted(x)) for x in shapes)}")
    aws, gcp, azure = federation.get("aws"), federation.get("gcp"), federation.get("azure")
    if aws is not None and not _ROLE_ARN_RE.fullmatch(aws["role_arn"]):
        raise _bad("invalid federation.aws.role_arn")
    if gcp is not None and not _GCP_AUDIENCE_RE.fullmatch(gcp["audience"]):
        raise _bad("federation.gcp.audience must be //iam.googleapis.com/projects/<n>/.../providers/<id>")
    if gcp is not None and "service_account" in gcp and not _GCP_SA_RE.fullmatch(gcp["service_account"]):
        raise _bad("federation.gcp.service_account must be a service account email")
    if azure is not None and "broker_url" in azure:
        broker = urlsplit(azure["broker_url"])
        if broker.scheme != "https" or not broker.hostname or broker.query or broker.fragment:
            raise _bad("federation.azure.broker_url must be an https URL")
        if not 1 <= len(azure["audience"]) <= 512:
            raise _bad("federation.azure.audience must be 1..512 characters")
    elif azure is not None:
        if not _TENANT_RE.fullmatch(azure["tenant_id"]):
            raise _bad("invalid federation.azure.tenant_id")
        try:
            uuid.UUID(azure["client_id"])
        except ValueError as exc:
            raise _bad("federation.azure.client_id must be a GUID") from exc
    needed = _CONNECTOR_FEDERATION.get(connector)
    if needed is not None and needed not in federation:
        raise _bad(f"connector {connector} requires federation.{needed}")


def validate_location(
    location: Mapping[str, Any], *, federation: Mapping[str, Any] | None = None, for_output: bool = False
) -> None:
    """Validate a dataset ``location`` (or a contract ``output.location`` when ``for_output``).

    Output locations carry their federation inline (``location.federation``, spec §8.1).
    """
    if not isinstance(location, Mapping):
        raise _bad("location must be an object")
    allowed = {"connector", "uri", "region", "endpoint"} | ({"federation"} if for_output else set())
    unknown = set(location) - allowed
    if unknown:
        raise _bad(f"unknown location members {sorted(unknown)}")
    parsed = _parse(location)
    effective = federation if federation is not None else location.get("federation") if for_output else None
    if effective is not None:
        _validate_federation(parsed.connector, effective)
    elif for_output and parsed.connector in _CONNECTOR_FEDERATION:
        raise _bad(f"output location with connector {parsed.connector} requires federation")


def federation_audience(connector: str, federation: Mapping[str, Any]) -> str | None:
    """The OIDC ``aud`` the cloud STS expects for this connector."""
    if connector == "s3":
        return AWS_AUDIENCE
    if connector == "azure-blob":
        azure = federation.get("azure") or {}
        return str(azure["audience"]) if "broker_url" in azure else AZURE_AUDIENCE
    if connector == "gcs":
        audience = str(federation["gcp"]["audience"])
        return "https:" + audience if audience.startswith("//") else audience
    if connector in ("https", "file"):
        return None
    raise _bad(f"unknown connector {connector!r}")


def _region(parsed: ParsedLocation) -> str:
    if parsed.region is None:
        raise ConfigError("s3 locations require a region")
    return parsed.region


def _build(parsed: ParsedLocation, **auth: Any) -> Connector:
    if parsed.connector == "file":
        return FileConnector(Path(parsed.host))
    if parsed.connector == "https":
        return HttpsConnector(parsed.host)
    if parsed.connector == "s3":
        from depa.storage.s3 import S3Connector, s3_client  # noqa: PLC0415 - boto3 is an optional extra

        client = s3_client(region=_region(parsed), endpoint_url=parsed.endpoint, credentials=auth.get("aws"))
        return S3Connector(client, parsed.host, parsed.prefix)
    token = auth.get("token")
    if not token:
        raise ConfigError(f"connector {parsed.connector} requires an access token")
    if parsed.connector == "gcs":
        return GcsConnector(bucket=parsed.host, prefix=parsed.prefix, token=token)
    return AzureBlobConnector(account_host=parsed.host, container=parsed.container, prefix=parsed.prefix, token=token)


def ccr_connector(
    location: Mapping[str, Any],
    federation: Mapping[str, Any] | None,
    token_source: TokenSource,
    *,
    allow_file: bool,
    session_name: str,
    write: bool = False,
) -> Connector:
    """Connector for use inside a CCR: credentials only via KMS OIDC token -> cloud STS (§10)."""
    location = {k: v for k, v in location.items() if k != "federation"}
    validate_location(location, federation=federation or {})
    parsed = _parse(location)
    if parsed.connector == "file" and not allow_file:
        raise ConfigError("the file connector is only allowed for development (virtual platform)")
    if parsed.connector == "https" and write:
        raise ConfigError("the https connector is read-only")
    if parsed.connector in ("file", "https"):
        return _build(parsed)
    audience = federation_audience(parsed.connector, federation or {})
    if federation is None or audience is None:  # unreachable after validation; fail closed anyway
        raise ConfigError(f"connector {parsed.connector} requires federation")
    oidc_token = token_source(audience)
    if parsed.connector == "s3":
        from depa.storage.s3 import aws_assume_role_with_web_identity  # noqa: PLC0415 - optional boto3

        creds = aws_assume_role_with_web_identity(
            oidc_token,
            role_arn=federation["aws"]["role_arn"],
            region=_region(parsed),
            role_session_name=session_name,
            endpoint_url=parsed.endpoint,
        )
        return _build(parsed, aws=creds)
    if parsed.connector == "gcs":
        token = gcp_sts_exchange(
            oidc_token,
            audience=federation["gcp"]["audience"],
            write=write,
            service_account=federation["gcp"].get("service_account"),
        )
        return _build(parsed, token=token)
    azure = federation["azure"]
    if "broker_url" in azure:  # spec §10.1 interim path (KMS tokens are ES256)
        grant = azure_sas_broker(
            oidc_token,
            broker_url=azure["broker_url"],
            account_host=parsed.host,
            container=parsed.container,
            prefix=parsed.prefix,
        )
        return AzureBlobConnector(
            account_host=parsed.host, container=parsed.container, prefix=parsed.prefix, sas=grant.sas
        )
    token = azure_client_assertion(oidc_token, tenant_id=azure["tenant_id"], client_id=azure["client_id"])
    return _build(parsed, token=token)


def party_connector(location: Mapping[str, Any], *, access_token: str | None = None) -> Connector:
    """Connector for a TDP/TDC using its own ambient credentials (S3) or a bearer token (GCS/Azure)."""
    location = {k: v for k, v in location.items() if k != "federation"}
    validate_location(location)
    parsed = _parse(location)
    if parsed.connector in ("gcs", "azure-blob") and not access_token:
        raise ConfigError(f"connector {parsed.connector} requires an access token for party-side access")
    return _build(parsed, token=access_token)
