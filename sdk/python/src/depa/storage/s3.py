"""Amazon S3 / S3-compatible (MinIO) connector and AWS web-identity federation, built on boto3.

On the CCR path the only credentials are those returned by ``AssumeRoleWithWebIdentity`` for
the KMS-issued OIDC token; AWS_* environment variables and ~/.aws files are never consulted."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import IO, Any

import boto3
import botocore.configprovider
import botocore.session
from boto3.s3.transfer import TransferConfig
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from depa.errors import StorageError
from depa.storage._common import DEFAULT_CHUNK, bounded_bytes, check_key, read_chunks

MIN_PART_SIZE = 5 << 20
AWS_SESSION_SECONDS = 3600
_SESSION_NAME_RE = re.compile(r"[^\w+=,.@-]")
_NOT_FOUND = frozenset({"NoSuchKey", "404", "NotFound", "NoSuchBucket"})


@dataclass(frozen=True)
class AwsCredentials:
    access_key_id: str
    secret_access_key: str = field(repr=False)
    session_token: str = field(repr=False)


def session_name(value: str) -> str:
    """Sanitise an STS role session name to ``[\\w+=,.@-]{2,64}``."""
    cleaned = _SESSION_NAME_RE.sub("-", value)[:64]
    return cleaned if len(cleaned) >= 2 else "depa-ccr"


def isolated_boto_session(region: str | None) -> Any:
    """A boto3 session that ignores AWS_* environment variables and ~/.aws files."""
    session_vars = {
        name: (spec[0], None, spec[2], spec[3])
        for name, spec in botocore.configprovider.BOTOCORE_DEFAUT_SESSION_VARIABLES.items()
    }
    session_vars["config_file"] = (None, None, os.devnull, None)
    session_vars["credentials_file"] = (None, None, os.devnull, None)
    core = botocore.session.Session(session_vars=session_vars)
    return boto3.session.Session(botocore_session=core, region_name=region)


def aws_assume_role_with_web_identity(
    web_identity_token: str,
    *,
    role_arn: str,
    region: str,
    role_session_name: str,
    endpoint_url: str | None = None,
) -> AwsCredentials:
    """``sts:AssumeRoleWithWebIdentity`` (unsigned call; MinIO uses its own endpoint)."""
    config = Config(
        signature_version=UNSIGNED,
        ignore_configured_endpoint_urls=True,
        retries={"max_attempts": 4, "mode": "standard"},
        connect_timeout=10,
        read_timeout=30,
    )
    sts = isolated_boto_session(region).client("sts", endpoint_url=endpoint_url, config=config, verify=True)
    try:
        response = sts.assume_role_with_web_identity(
            RoleArn=role_arn,
            RoleSessionName=session_name(role_session_name),
            WebIdentityToken=web_identity_token,
            DurationSeconds=AWS_SESSION_SECONDS,
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "unknown")
        raise StorageError(f"AWS STS AssumeRoleWithWebIdentity failed: {code}", code="federation") from exc
    except BotoCoreError as exc:
        raise StorageError(f"AWS STS unreachable ({type(exc).__name__})", code="federation") from exc
    creds = response.get("Credentials") or {}
    try:
        return AwsCredentials(
            access_key_id=str(creds["AccessKeyId"]),
            secret_access_key=str(creds["SecretAccessKey"]),
            session_token=str(creds["SessionToken"]),
        )
    except KeyError as exc:
        raise StorageError("AWS STS response has no credentials", code="federation") from exc


def s3_client(*, region: str, endpoint_url: str | None, credentials: AwsCredentials | None) -> Any:
    """``credentials=None`` uses the caller's ambient AWS credential chain (party side only)."""
    config = Config(
        retries={"max_attempts": 4, "mode": "standard"},
        connect_timeout=10,
        read_timeout=60,
        s3={"addressing_style": "path"} if endpoint_url else {},
        ignore_configured_endpoint_urls=credentials is not None,
    )
    if credentials is None:
        ambient = boto3.session.Session(region_name=region)
        return ambient.client("s3", endpoint_url=endpoint_url, config=config)
    return isolated_boto_session(region).client(
        "s3",
        endpoint_url=endpoint_url,
        config=config,
        verify=True,
        aws_access_key_id=credentials.access_key_id,
        aws_secret_access_key=credentials.secret_access_key,
        aws_session_token=credentials.session_token,
    )


class _ExactReader:
    """Feeds exactly ``size`` bytes of ``src`` to boto3, failing on short or long sources."""

    def __init__(self, src: IO[bytes], size: int) -> None:
        self._chunks = read_chunks(src, size, 1 << 20)
        self._buffer = b""

    def read(self, n: int = -1) -> bytes:
        out: list[bytes] = []
        want = n if n is not None and n >= 0 else 1 << 62
        while want > 0:
            if not self._buffer:
                self._buffer = next(self._chunks, b"")
                if not self._buffer:
                    break
            piece = self._buffer[:want]
            self._buffer = self._buffer[len(piece) :]
            out.append(piece)
            want -= len(piece)
        return b"".join(out)


class S3Connector:
    connector = "s3"

    def __init__(self, client: Any, bucket: str, prefix: str, *, part_size: int = DEFAULT_CHUNK) -> None:
        if part_size < MIN_PART_SIZE:
            raise StorageError("S3 part size must be at least 5 MiB", code="bad_config")
        self._client = client
        self._bucket = bucket
        self._prefix = prefix
        self._part_size = part_size

    def _key(self, key: str) -> str:
        return self._prefix + check_key(key)

    def _error(self, exc: Exception, key: str) -> StorageError:
        if isinstance(exc, ClientError):
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in _NOT_FOUND:
                return StorageError(f"object {key} not found", code="not_found")
            return StorageError(f"S3 error for {key}: {code or 'unknown'}")
        return StorageError(f"S3 transport error for {key} ({type(exc).__name__})", code="transport")

    @contextmanager
    def open_read(self, key: str) -> Iterator[IO[bytes]]:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=self._key(key))
        except (ClientError, BotoCoreError) as exc:
            raise self._error(exc, key) from exc
        body = response["Body"]
        try:
            yield body
        finally:
            body.close()

    def read_bytes(self, key: str, max_bytes: int) -> bytes:
        with self.open_read(key) as body:
            return bounded_bytes(body, max_bytes, f"object {key}")

    def upload(self, key: str, src: IO[bytes], size: int) -> None:
        config = TransferConfig(
            multipart_threshold=self._part_size, multipart_chunksize=self._part_size, use_threads=False
        )
        object_key = self._key(key)
        try:
            self._client.upload_fileobj(_ExactReader(src, size), self._bucket, object_key, Config=config)
        except StorageError:
            raise
        except (ClientError, BotoCoreError) as exc:
            raise self._error(exc, key) from exc

    def uri_for(self, key: str) -> str:
        return f"s3://{self._bucket}/{self._key(key)}"
