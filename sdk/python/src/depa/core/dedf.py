"""Encrypted dataset format ``dedf/1`` (spec §7.3).

Streaming, constant-memory (about two segments) encryption and decryption of objects, the
dataset manifest, DEK wrapping (HPKE) and the detached manifest JWS.

Object layout: 38-byte header || segments. Segment ``i`` is AES-256-GCM under the per-file key
with nonce ``nonce_prefix(7) || u32be(i) || last_flag`` and AAD ``SHA-256(header)``. The final
segment carries ``last_flag = 0x01``; truncation, extension, reordering and any tag failure
make decryption fail.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import struct
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from depa.core import hpke
from depa.core.encoding import b64url_decode, b64url_encode, canonicalize, digest, loads_object
from depa.core.ids import check_dataset_id, check_party_id, dataset_owner, parse_recipient
from depa.core.jws import KeyLookup, sign_compact, verify_compact
from depa.core.keys import Signer
from depa.core.timeutil import now_rfc3339, parse_rfc3339
from depa.errors import CryptoError, ValidationError, VerificationError

FORMAT = "dedf/1"
MAGIC = b"DEDF1\x00"
VERSION_BYTE = 0x01
HEADER_LEN = 38
TAG_LEN = 16
SALT_LEN = 16
NONCE_PREFIX_LEN = 7
DEK_LEN = 32
DEFAULT_SEGMENT_SIZE = 1 << 20
MAX_SEGMENT_SIZE = 64 << 20
MAX_SEGMENTS = 1 << 32
MAX_FILES = 1_000_000
MANIFEST_TYP = "depa-manifest+jws"
MANIFEST_NAME = "manifest.json"
MANIFEST_JWS_NAME = "manifest.jws"
MAX_MANIFEST_BYTES = 64 << 20

_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SEGMENT_RE = re.compile(r"^[^\x00-\x1f\x7f\\]{1,255}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------------------------
# Paths and key derivation


def validate_relpath(path: object) -> str:
    """A dataset-relative POSIX path: no absolute paths, ``.``/``..``, empty or control segments."""
    if not isinstance(path, str) or not path or len(path) > 1024:
        raise ValidationError(f"invalid object path {path!r}", code="bad_path")
    parts = path.split("/")
    for part in parts:
        if part in ("", ".", "..") or not _SEGMENT_RE.fullmatch(part):
            raise ValidationError(f"invalid object path {path!r}", code="bad_path")
    return path


def object_key(path: str) -> str:
    return f"objects/{validate_relpath(path)}.dedf"


def dek_info(dataset_id: str, version: str) -> bytes:
    return f"depa-training/dedf/1/dek|{dataset_id}|{version}".encode()


def derive_file_key(dek: bytes, salt: bytes, path: str) -> bytes:
    if len(dek) != DEK_LEN or len(salt) != SALT_LEN:
        raise CryptoError("bad DEK or salt length")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=f"depa-training/dedf/1/file|{path}".encode(),
    ).derive(dek)


def build_header(segment_size: int, salt: bytes, nonce_prefix: bytes) -> bytes:
    if not 1 <= segment_size <= MAX_SEGMENT_SIZE:
        raise ValidationError("segment_size out of range", code="bad_segment_size")
    if len(salt) != SALT_LEN or len(nonce_prefix) != NONCE_PREFIX_LEN:
        raise CryptoError("bad salt / nonce prefix length")
    header = MAGIC + bytes([VERSION_BYTE]) + struct.pack(">I", segment_size) + salt + nonce_prefix + b"\x00" * 4
    return header


def parse_header(header: bytes) -> tuple[int, bytes, bytes]:
    if len(header) != HEADER_LEN:
        raise CryptoError("truncated dedf header", code="dedf_header")
    if header[:6] != MAGIC or header[6] != VERSION_BYTE or header[34:38] != b"\x00" * 4:
        raise CryptoError("bad dedf header", code="dedf_header")
    (segment_size,) = struct.unpack(">I", header[7:11])
    if not 1 <= segment_size <= MAX_SEGMENT_SIZE:
        raise CryptoError("bad dedf segment size", code="dedf_header")
    return segment_size, header[11:27], header[27:34]


def _nonce(prefix: bytes, index: int, last: bool) -> bytes:
    if index >= MAX_SEGMENTS:
        raise CryptoError("too many segments", code="dedf_segments")
    return prefix + struct.pack(">I", index) + (b"\x01" if last else b"\x00")


def ciphertext_size_options(plaintext_size: int, segment_size: int) -> set[int]:
    """Valid ciphertext sizes: the last segment may be full or an extra empty one."""
    full, rem = divmod(plaintext_size, segment_size)
    options = {HEADER_LEN + plaintext_size + (full + 1) * TAG_LEN}
    if rem == 0 and full > 0:
        options.add(HEADER_LEN + plaintext_size + full * TAG_LEN)
    return options


def _read_upto(src: IO[bytes], size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = src.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


# ---------------------------------------------------------------------------------------------
# Objects


@dataclass(frozen=True)
class FileEntry:
    path: str
    plaintext_size: int
    ciphertext_size: int
    ciphertext_sha256: str

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "plaintext_size": self.plaintext_size,
            "ciphertext_size": self.ciphertext_size,
            "ciphertext_sha256": self.ciphertext_sha256,
        }


def encrypt_stream(
    dek: bytes,
    path: str,
    src: IO[bytes],
    dst: IO[bytes],
    *,
    segment_size: int = DEFAULT_SEGMENT_SIZE,
    progress: Callable[[int], None] | None = None,
) -> FileEntry:
    """Encrypt one object; ``progress`` receives the plaintext bytes done after each segment."""
    validate_relpath(path)
    salt, nonce_prefix = os.urandom(SALT_LEN), os.urandom(NONCE_PREFIX_LEN)
    header = build_header(segment_size, salt, nonce_prefix)
    aad = hashlib.sha256(header).digest()
    aead = AESGCM(derive_file_key(dek, salt, path))
    hasher = hashlib.sha256(header)
    dst.write(header)
    ct_size, pt_size, index = HEADER_LEN, 0, 0
    current = _read_upto(src, segment_size)
    while True:
        following = _read_upto(src, segment_size) if len(current) == segment_size else b""
        last = not following
        sealed = aead.encrypt(_nonce(nonce_prefix, index, last), current, aad)
        dst.write(sealed)
        hasher.update(sealed)
        ct_size += len(sealed)
        pt_size += len(current)
        index += 1
        if progress is not None:
            progress(pt_size)
        if last:
            break
        current = following
    return FileEntry(path, pt_size, ct_size, hasher.hexdigest())


def decrypt_stream(
    dek: bytes,
    entry: FileEntry,
    src: IO[bytes],
    dst: IO[bytes],
    *,
    segment_size: int,
) -> None:
    """Decrypt one object, verifying header, every segment, completeness, size and hash.

    Plaintext is written incrementally; on any exception the caller MUST discard ``dst``.
    """
    header = _read_upto(src, HEADER_LEN)
    hdr_segment, salt, nonce_prefix = parse_header(header)
    if hdr_segment != segment_size:
        raise CryptoError("object segment size differs from the manifest", code="dedf_header")
    aad = hashlib.sha256(header).digest()
    aead = AESGCM(derive_file_key(dek, salt, entry.path))
    hasher = hashlib.sha256(header)
    chunk = segment_size + TAG_LEN
    budget = entry.ciphertext_size - HEADER_LEN
    if budget < TAG_LEN:
        raise CryptoError("ciphertext size below minimum", code="dedf_size")

    def read_chunk() -> bytes:
        nonlocal budget
        # Read one byte past the declared size so appended data is detected without unbounded reads.
        data = _read_upto(src, min(chunk, budget + 1))
        budget -= len(data)
        if budget < 0:
            raise CryptoError("object longer than declared", code="dedf_extra_data")
        return data

    current = read_chunk()
    index, pt_size = 0, 0
    while True:
        following = read_chunk() if len(current) == chunk else b""
        last = not following
        if len(current) < TAG_LEN:
            raise CryptoError("missing or truncated final segment", code="dedf_truncated")
        try:
            plaintext = aead.decrypt(_nonce(nonce_prefix, index, last), current, aad)
        except InvalidTag as exc:
            raise CryptoError("segment authentication failed", code="dedf_tag") from exc
        if (not last and len(plaintext) != segment_size) or len(plaintext) > segment_size:
            raise CryptoError("segment has the wrong size", code="dedf_segments")
        hasher.update(current)
        dst.write(plaintext)
        pt_size += len(plaintext)
        index += 1
        if last:
            break
        current = following
    if src.read(1):
        raise CryptoError("trailing data after final segment", code="dedf_extra_data")
    if hasher.hexdigest() != entry.ciphertext_sha256:
        raise CryptoError("ciphertext_sha256 mismatch", code="dedf_hash")
    if pt_size != entry.plaintext_size or budget != 0:
        raise CryptoError("object size mismatch", code="dedf_size")


def decrypt_to_path(
    dek: bytes, entry: FileEntry, src: IO[bytes], dest: Path, *, segment_size: int, mode: int = 0o600
) -> None:
    """Decrypt into ``dest`` atomically (temp file + rename); nothing is left behind on failure."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.partial")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb") as out:
            decrypt_stream(dek, entry, src, out, segment_size=segment_size)
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------------------------
# DEK wrapping


def new_dek() -> bytes:
    return os.urandom(DEK_LEN)


def wrap_dek(dek: bytes, public_key: bytes, *, kid: str, dataset_id: str, version: str) -> dict[str, str]:
    enc, ct = hpke.seal_base(public_key, info=dek_info(dataset_id, version), aad=b"", plaintext=dek)
    return {"kid": kid, "enc": b64url_encode(enc), "ct": b64url_encode(ct)}


def unwrap_dek(wrapped: Mapping[str, Any], private_key: bytes, *, dataset_id: str, version: str) -> bytes:
    try:
        enc = b64url_decode(str(wrapped["enc"]))
        ct = b64url_decode(str(wrapped["ct"]))
    except (KeyError, ValidationError) as exc:
        raise CryptoError("malformed wrapped DEK", code="bad_wrapped_dek") from exc
    dek = hpke.open_base(private_key, enc=enc, info=dek_info(dataset_id, version), aad=b"", ciphertext=ct)
    if len(dek) != DEK_LEN:
        raise CryptoError("unwrapped DEK has the wrong length", code="bad_wrapped_dek")
    return dek


@dataclass(frozen=True)
class Recipient:
    """An output recipient: ``party:<id>#<kid>`` or ``kms:<kid>`` and its X25519 public key."""

    recipient: str
    kid: str
    public_key: bytes


def wrap_dek_for_recipients(
    dek: bytes, recipients: Sequence[Recipient], *, dataset_id: str, version: str
) -> list[dict[str, str]]:
    if not recipients:
        raise ValidationError("at least one output recipient is required", code="no_recipients")
    out = []
    for r in recipients:
        parse_recipient(r.recipient)
        w = wrap_dek(dek, r.public_key, kid=r.kid, dataset_id=dataset_id, version=version)
        out.append({"recipient": r.recipient, **w})
    return out


# ---------------------------------------------------------------------------------------------
# Manifest


@dataclass(frozen=True)
class Manifest:
    raw: dict[str, Any]
    dataset_id: str
    version: str
    owner: str
    segment_size: int
    files: tuple[FileEntry, ...]

    @property
    def digest(self) -> str:
        return digest(self.raw)

    @property
    def total_ciphertext_size(self) -> int:
        return sum(f.ciphertext_size for f in self.files)

    @property
    def total_plaintext_size(self) -> int:
        return sum(f.plaintext_size for f in self.files)

    def wrapped_dek(self) -> dict[str, Any]:
        value = self.raw.get("wrapped_dek")
        if not isinstance(value, dict):
            raise ValidationError("manifest has no wrapped_dek", code="bad_manifest")
        return value

    def wrapped_dek_for(self, recipient: str) -> dict[str, Any]:
        for entry in self.raw.get("wrapped_deks", []):
            if entry.get("recipient") == recipient:
                return dict(entry)
        raise ValidationError(f"manifest has no DEK for recipient {recipient}", code="not_a_recipient")


def build_manifest(
    *,
    dataset_id: str,
    version: str,
    owner: str,
    segment_size: int,
    files: Iterable[FileEntry],
    wrapped_dek: Mapping[str, str] | None = None,
    wrapped_deks: Sequence[Mapping[str, str]] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    if (wrapped_dek is None) == (wrapped_deks is None):
        raise ValidationError("exactly one of wrapped_dek / wrapped_deks is required", code="bad_manifest")
    manifest: dict[str, Any] = {
        "format": FORMAT,
        "dataset_id": dataset_id,
        "version": version,
        "owner": owner,
        "created_at": created_at or now_rfc3339(),
        "segment_size": segment_size,
        "files": [f.to_json() for f in sorted(files, key=lambda f: f.path)],
    }
    if wrapped_dek is not None:
        manifest["wrapped_dek"] = dict(wrapped_dek)
    elif wrapped_deks is not None:
        manifest["wrapped_deks"] = [dict(w) for w in wrapped_deks]
    parse_manifest(manifest, output=wrapped_deks is not None)
    return manifest


def _is_nat(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _check_wrapped(entry: object, *, with_recipient: bool) -> None:
    expected = {"kid", "enc", "ct"} | ({"recipient"} if with_recipient else set())
    if not isinstance(entry, dict) or set(entry) != expected:
        raise ValidationError("malformed wrapped DEK entry", code="bad_manifest")
    if not all(isinstance(entry[k], str) and entry[k] for k in expected):
        raise ValidationError("malformed wrapped DEK entry", code="bad_manifest")
    if len(b64url_decode(entry["enc"])) != hpke.ENC_SIZE or len(b64url_decode(entry["ct"])) != DEK_LEN + TAG_LEN:
        raise ValidationError("wrapped DEK has the wrong size", code="bad_manifest")
    if with_recipient:
        parse_recipient(entry["recipient"])


def parse_manifest(
    obj: object,
    *,
    expected_dataset_id: str | None = None,
    expected_version: str | None = None,
    output: bool = False,
) -> Manifest:
    """Strictly validate a manifest object. ``output`` selects the CCR output variant."""
    if not isinstance(obj, dict):
        raise ValidationError("manifest must be an object", code="bad_manifest")
    dek_field = "wrapped_deks" if output else "wrapped_dek"
    allowed = {"format", "dataset_id", "version", "owner", "created_at", "segment_size", "files", dek_field}
    if set(obj) != allowed:
        raise ValidationError(f"manifest members must be exactly {sorted(allowed)}", code="bad_manifest")
    if obj["format"] != FORMAT:
        raise ValidationError("unsupported manifest format", code="bad_manifest")
    dataset_id, version, owner = obj["dataset_id"], obj["version"], obj["owner"]
    if output:
        if not isinstance(dataset_id, str) or len(dataset_id.split("/")) != 4 or dataset_id.split("/")[1] != "outputs":
            raise ValidationError(
                "output manifest dataset_id must be <tdc>/outputs/<contract>/<run>", code="bad_manifest"
            )
        check_party_id(dataset_id.split("/")[0])
        if owner != dataset_id.split("/")[0]:
            raise ValidationError("output manifest owner must be the TDC", code="bad_manifest")
    else:
        check_dataset_id(dataset_id)
        if owner != dataset_owner(dataset_id):
            raise ValidationError("manifest owner must be the dataset_id owner prefix", code="bad_manifest")
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        raise ValidationError("invalid manifest version", code="bad_manifest")
    if expected_dataset_id is not None and dataset_id != expected_dataset_id:
        raise VerificationError("manifest dataset_id mismatch", code="manifest_mismatch")
    if expected_version is not None and version != expected_version:
        raise VerificationError("manifest version mismatch", code="manifest_mismatch")
    parse_rfc3339(obj["created_at"])
    segment_size = obj["segment_size"]
    if not _is_nat(segment_size) or not 1 <= segment_size <= MAX_SEGMENT_SIZE:
        raise ValidationError("invalid segment_size", code="bad_manifest")
    if output:
        deks = obj["wrapped_deks"]
        if not isinstance(deks, list) or not deks:
            raise ValidationError("wrapped_deks must be a non-empty list", code="bad_manifest")
        for entry in deks:
            _check_wrapped(entry, with_recipient=True)
        if len({e["recipient"] for e in deks}) != len(deks):
            raise ValidationError("duplicate output recipient", code="bad_manifest")
    else:
        _check_wrapped(obj["wrapped_dek"], with_recipient=False)
    files_raw = obj["files"]
    if not isinstance(files_raw, list) or not files_raw or len(files_raw) > MAX_FILES:
        raise ValidationError("manifest files must be a non-empty list", code="bad_manifest")
    files: list[FileEntry] = []
    seen: set[str] = set()
    for f in files_raw:
        if not isinstance(f, dict) or set(f) != {
            "path",
            "plaintext_size",
            "ciphertext_size",
            "ciphertext_sha256",
        }:
            raise ValidationError("malformed manifest file entry", code="bad_manifest")
        path = validate_relpath(f["path"])
        if path in seen:
            raise ValidationError(f"duplicate path {path!r}", code="bad_manifest")
        seen.add(path)
        pt, ct, sha = f["plaintext_size"], f["ciphertext_size"], f["ciphertext_sha256"]
        if not _is_nat(pt) or not _is_nat(ct) or not isinstance(sha, str) or not _HEX64_RE.fullmatch(sha):
            raise ValidationError("malformed manifest file entry", code="bad_manifest")
        if ct not in ciphertext_size_options(pt, segment_size):
            raise ValidationError(f"inconsistent sizes for {path!r}", code="bad_manifest")
        files.append(FileEntry(path, pt, ct, sha))
    # A path must not also be a directory prefix of another path.
    for path in seen:
        for parent in PurePosixPath(path).parents:
            if str(parent) in seen:
                raise ValidationError(f"path {path!r} conflicts with file {parent}", code="bad_manifest")
    return Manifest(dict(obj), dataset_id, version, owner, segment_size, tuple(files))


def manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    return canonicalize(dict(manifest))


def sign_manifest(manifest: Mapping[str, Any], signer: Signer) -> str:
    """Detached-payload JWS over JCS(manifest), ``typ = depa-manifest+jws``."""
    return sign_compact(signer, manifest_bytes(manifest), typ=MANIFEST_TYP, detached=True)


def verify_manifest(
    manifest_raw: bytes,
    manifest_jws: str,
    key_for_kid: KeyLookup,
    *,
    expected_digest: str | None,
    expected_signer_prefix: str | None = None,
    expected_dataset_id: str | None = None,
    expected_version: str | None = None,
    output: bool = False,
) -> Manifest:
    """Parse + verify: strict JSON, schema, JWS by the owner (``<owner>#kid``) and digest binding."""
    if len(manifest_raw) > MAX_MANIFEST_BYTES:
        raise ValidationError("manifest too large", code="bad_manifest")
    manifest = parse_manifest(
        loads_object(manifest_raw),
        expected_dataset_id=expected_dataset_id,
        expected_version=expected_version,
        output=output,
    )
    verified = verify_compact(
        manifest_jws.strip(), key_for_kid, typ=MANIFEST_TYP, detached_payload=manifest_bytes(manifest.raw)
    )
    kid = str(verified.header["kid"])
    prefix = expected_signer_prefix if expected_signer_prefix is not None else f"{manifest.owner}#"
    if not kid.startswith(prefix):
        raise VerificationError("manifest is not signed by the expected party", code="manifest_signer")
    if expected_digest is not None and manifest.digest != expected_digest:
        raise VerificationError("manifest_digest does not match the bound value", code="manifest_digest")
    return manifest


# ---------------------------------------------------------------------------------------------
# Directory helpers (TDP side)


def iter_plain_files(root: Path) -> Iterator[tuple[str, Path]]:
    """Yield ``(relpath, path)`` for regular files below ``root``; symlinks are rejected."""
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames) + [d for d in dirnames if (Path(dirpath) / d).is_symlink()]:
            full = Path(dirpath) / name
            if full.is_symlink():
                raise ValidationError(f"symlinks are not allowed in datasets: {full}", code="bad_path")
            if not full.is_file():
                raise ValidationError(f"not a regular file: {full}", code="bad_path")
            yield validate_relpath(full.relative_to(root).as_posix()), full


def matches_any(relpath: str, patterns: Sequence[str]) -> bool:
    """Glob match over a relative path; ``*`` spans directories and a leading ``**/`` is optional."""
    for pattern in patterns:
        if fnmatch.fnmatchcase(relpath, pattern):
            return True
        if pattern.startswith("**/") and fnmatch.fnmatchcase(relpath, pattern[3:]):
            return True
    return False


def _file_progress(report: Callable[[str, int, int], None], rel: str, total: int) -> Callable[[int], None]:
    return lambda done: report(rel, done, total)


def encrypt_directory(
    src_dir: Path,
    out_dir: Path,
    *,
    dataset_id: str,
    version: str,
    kms_kid: str,
    kms_public_key: bytes,
    signer: Signer,
    segment_size: int = DEFAULT_SEGMENT_SIZE,
    include: Sequence[str] | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    """Encrypt files below ``src_dir`` into ``out_dir`` (``manifest.json``, ``.jws``, ``objects/``).

    ``include`` optionally restricts the files to glob patterns over the relative path;
    ``progress(path, bytes_done, total_bytes)`` is called per file as it is encrypted.
    """
    owner = dataset_owner(check_dataset_id(dataset_id))
    if not signer.kid.startswith(f"{owner}#"):
        raise ValidationError("the manifest must be signed by the dataset owner", code="bad_signer")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValidationError(f"output directory {out_dir} is not empty", code="out_dir_not_empty")
    dek = new_dek()
    entries = []
    for rel, path in iter_plain_files(src_dir):
        if include is not None and not matches_any(rel, include):
            continue
        target = out_dir / object_key(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        on_segment = None
        if progress is not None:
            total = path.stat().st_size
            progress(rel, 0, total)
            on_segment = _file_progress(progress, rel, total)
        with path.open("rb") as src, target.open("xb") as dst:
            entries.append(encrypt_stream(dek, rel, src, dst, segment_size=segment_size, progress=on_segment))
    if not entries:
        raise ValidationError(f"no files found in {src_dir}", code="empty_dataset")
    manifest = build_manifest(
        dataset_id=dataset_id,
        version=version,
        owner=owner,
        segment_size=segment_size,
        files=entries,
        wrapped_dek=wrap_dek(dek, kms_public_key, kid=kms_kid, dataset_id=dataset_id, version=version),
    )
    (out_dir / MANIFEST_NAME).write_bytes(manifest_bytes(manifest))
    (out_dir / MANIFEST_JWS_NAME).write_text(sign_manifest(manifest, signer))
    return manifest
