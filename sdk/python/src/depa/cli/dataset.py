"""``depa dataset``: TDP dataset keys, release policy, encryption, upload and catalog entry."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from depa.cli.common import STATE, emit, handle_errors, read_env_secret
from depa.contract.authoring import DatasetTemplate, load_dataset_template
from depa.contract.schema import require_schema
from depa.contract.yamlio import load_document
from depa.core.dedf import DEFAULT_SEGMENT_SIZE
from depa.errors import ValidationError
from depa.storage.factory import party_connector
from depa.workflows import encrypt_dataset, load_encrypted, publish_dataset, upload_directory

app = typer.Typer(help="Datasets: keys, release policies, dedf/1 encryption, upload, catalog.", no_args_is_help=True)
policy_app = typer.Typer(help="Release Policy v1 (spec §7.1).", no_args_is_help=True)
app.add_typer(policy_app, name="policy")

TemplateArg = Annotated[Path, typer.Argument(help="Dataset template (scenarios/<name>/datasets/<x>.yaml)")]


def _template(path: Path) -> DatasetTemplate:
    return load_dataset_template(path, os.environ)


@app.command("create-key")
@handle_errors
def create_key(
    template: TemplateArg,
    description: Annotated[str, typer.Option(help="Free-text description")] = "",
) -> None:
    """Create the dataset record and its HPKE key in the KMS (``<dataset_id>#1``)."""
    t = _template(template)
    profile = STATE.profile()
    record = profile.kms_client().create_dataset(
        profile.signer(), t.dataset_id, description or str(t.descriptor.get("name", ""))
    )
    emit(record.raw, f"{record.dataset_id}: keys {[k.kid for k in record.keys]}")


@policy_app.command("set")
@handle_errors
def policy_set(
    template: TemplateArg,
    policy_file: Annotated[
        Path | None, typer.Option("--policy", help="Policy YAML/JSON instead of the template's")
    ] = None,
) -> None:
    """Set (a new version of) the dataset's release policy. Only the owner can."""
    t = _template(template)
    policy = load_document(policy_file, os.environ) if policy_file else t.policy
    if policy is None:
        raise ValidationError("no policy given (template has no `policy:` section)", code="no_policy")
    require_schema(policy, "release-policy-v1", "release policy")
    profile = STATE.profile()
    record = profile.kms_client().set_policy(profile.signer(), t.dataset_id, policy)
    emit(record.raw, f"{t.dataset_id}: policy version {record.raw.get('policy_version')}")


@policy_app.command("show")
@handle_errors
def policy_show(dataset_id: Annotated[str, typer.Argument()]) -> None:
    """Show the current policy and key states."""
    record = STATE.profile().kms_client().get_dataset(dataset_id)
    emit(record.raw)


@app.command("encrypt")
@handle_errors
def encrypt(
    template: TemplateArg,
    out: Annotated[Path, typer.Option(help="Output directory (must be empty)")],
    source: Annotated[Path | None, typer.Option(help="Plaintext directory (default: template source.path)")] = None,
    segment_size: Annotated[int, typer.Option(help="dedf/1 segment size in bytes")] = DEFAULT_SEGMENT_SIZE,
) -> None:
    """Client-side encrypt to the dataset's current KMS key; writes manifest.json/.jws and objects/."""
    t = _template(template)
    profile = STATE.profile()
    manifest = encrypt_dataset(
        t,
        kms=profile.kms_client(),
        signer=profile.signer(),
        out_dir=out,
        source=source,
        segment_size=segment_size,
    )
    emit(
        {
            "manifest_digest": manifest.digest,
            "files": len(manifest.files),
            "bytes": manifest.total_ciphertext_size,
        },
        f"encrypted {len(manifest.files)} files; manifest_digest {manifest.digest}",
    )


@app.command("upload")
@handle_errors
def upload(
    template: TemplateArg,
    encrypted: Annotated[Path, typer.Option(help="Directory produced by `depa dataset encrypt`")],
    access_token_env: Annotated[
        str | None, typer.Option(help="Env var holding a bearer token (gcs / azure-blob)")
    ] = None,
) -> None:
    """Upload to the template's storage location with your own credentials (manifest written last)."""
    t = _template(template)
    _, _, manifest = load_encrypted(encrypted)
    if manifest.dataset_id != t.dataset_id or manifest.version != t.version:
        raise ValidationError("encrypted directory belongs to a different dataset/version", code="manifest_mismatch")
    connector = party_connector(t.descriptor["location"], access_token=read_env_secret(access_token_env))
    count = upload_directory(connector, encrypted, manifest)
    location = str(t.descriptor["location"]["uri"])
    emit({"uploaded": count, "location": location}, f"uploaded {count} objects to {location}")


@app.command("publish")
@handle_errors
def publish(
    template: TemplateArg,
    encrypted: Annotated[Path, typer.Option(help="Directory produced by `depa dataset encrypt`")],
    records: Annotated[int | None, typer.Option(help="Record count to advertise")] = None,
) -> None:
    """Publish the (immutable) dataset version descriptor to the CS catalog."""
    t = _template(template)
    profile = STATE.profile()
    descriptor = publish_dataset(profile.cs_client(), profile.signer(), t, encrypted, records=records)
    emit(descriptor, f"published {t.dataset_id}@{t.version} ({descriptor['manifest_digest']})")


@app.command("rotate")
@handle_errors
def rotate(
    dataset_id: Annotated[str, typer.Argument()],
    retire_previous: Annotated[bool, typer.Option(help="Make old keys unreleasable")] = False,
) -> None:
    """Create key version #n+1 (new versions encrypt to it)."""
    profile = STATE.profile()
    emit(profile.kms_client().rotate_dataset(profile.signer(), dataset_id, retire_previous=retire_previous))


@app.command("suspend")
@handle_errors
def suspend(dataset_id: Annotated[str, typer.Argument()], reason: Annotated[str, typer.Option()] = "") -> None:
    """Owner kill switch: immediately stop all key releases."""
    profile = STATE.profile()
    emit(profile.kms_client().suspend_dataset(profile.signer(), dataset_id, reason))


@app.command("resume")
@handle_errors
def resume(dataset_id: Annotated[str, typer.Argument()], reason: Annotated[str, typer.Option()] = "") -> None:
    """Lift the owner suspension (a DC suspension stays in force)."""
    profile = STATE.profile()
    emit(profile.kms_client().resume_dataset(profile.signer(), dataset_id, reason))
