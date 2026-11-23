"""dedf/1: roundtrips, manifest + JWS, and every tamper / truncation / reorder case (spec §7.3)."""

from __future__ import annotations

import hashlib
import io
import struct

import pytest

from depa.core import dedf
from depa.core.jws import jwks_lookup
from depa.core.keys import EcSigner, HpkeKeyPair
from depa.errors import CryptoError, ValidationError, VerificationError

S = 64


def _enc(data: bytes, dek: bytes, path: str = "a/b.csv", seg: int = S) -> tuple[bytes, dedf.FileEntry]:
    out = io.BytesIO()
    entry = dedf.encrypt_stream(dek, path, io.BytesIO(data), out, segment_size=seg)
    return out.getvalue(), entry


def _dec(blob: bytes, dek: bytes, entry: dedf.FileEntry, seg: int = S) -> bytes:
    out = io.BytesIO()
    dedf.decrypt_stream(dek, entry, io.BytesIO(blob), out, segment_size=seg)
    return out.getvalue()


def _with(entry: dedf.FileEntry, blob: bytes) -> dedf.FileEntry:
    """Entry describing a tampered blob, so only the cryptographic checks can catch it."""
    return dedf.FileEntry(entry.path, entry.plaintext_size, len(blob), hashlib.sha256(blob).hexdigest())


@pytest.mark.parametrize("size", [0, 1, S - 1, S, S + 1, 3 * S, 3 * S + 7])
def test_roundtrip_sizes(size: int) -> None:
    dek = dedf.new_dek()
    data = bytes(range(256)) * (size // 256 + 1)
    data = data[:size]
    blob, entry = _enc(data, dek)
    assert entry.plaintext_size == size and entry.ciphertext_size == len(blob)
    assert entry.ciphertext_size in dedf.ciphertext_size_options(size, S)
    assert len(blob) == dedf.HEADER_LEN + size + max(1, -(-size // S)) * dedf.TAG_LEN
    assert _dec(blob, dek, entry) == data


def test_header_layout() -> None:
    blob, _ = _enc(b"x", dedf.new_dek())
    assert blob[:6] == b"DEDF1\x00" and blob[6] == 1
    assert struct.unpack(">I", blob[7:11])[0] == S and blob[34:38] == b"\x00" * 4


def test_reader_accepts_trailing_empty_last_segment() -> None:
    """A writer may end a multiple-of-segment file with an empty final segment."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    dek, data = dedf.new_dek(), b"z" * (2 * S)
    salt, prefix = b"s" * 16, b"p" * 7
    header = dedf.build_header(S, salt, prefix)
    aad = hashlib.sha256(header).digest()
    aead = AESGCM(dedf.derive_file_key(dek, salt, "f"))
    segs = [
        aead.encrypt(prefix + struct.pack(">I", i) + (b"\x01" if last else b"\x00"), chunk, aad)
        for i, (chunk, last) in enumerate([(data[:S], False), (data[S:], False), (b"", True)])
    ]
    blob = header + b"".join(segs)
    entry = dedf.FileEntry("f", len(data), len(blob), hashlib.sha256(blob).hexdigest())
    assert _dec(blob, dek, entry) == data


@pytest.fixture
def sample() -> tuple[bytes, bytes, dedf.FileEntry, bytes]:
    dek = dedf.new_dek()
    data = b"0123456789abcdef" * 20  # 320 bytes = 5 segments
    blob, entry = _enc(data, dek)
    return dek, blob, entry, data


def _segments(blob: bytes) -> list[bytes]:
    body = blob[dedf.HEADER_LEN :]
    return [body[i : i + S + dedf.TAG_LEN] for i in range(0, len(body), S + dedf.TAG_LEN)]


def test_tamper_segment_byte(sample) -> None:
    dek, blob, entry, _ = sample
    bad = bytearray(blob)
    bad[dedf.HEADER_LEN + 5] ^= 1
    with pytest.raises(CryptoError) as e:
        _dec(bytes(bad), dek, _with(entry, bytes(bad)))
    assert e.value.code == "dedf_tag"
    with pytest.raises(CryptoError):  # and with the honest manifest entry
        _dec(bytes(bad), dek, entry)


def test_tamper_header(sample) -> None:
    dek, blob, entry, _ = sample
    for offset in (0, 6, 12, 30, 36):  # magic, version, salt, nonce prefix, reserved
        bad = bytearray(blob)
        bad[offset] ^= 1
        with pytest.raises(CryptoError):
            _dec(bytes(bad), dek, _with(entry, bytes(bad)))


def test_truncation_drop_last_segment(sample) -> None:
    dek, blob, entry, _ = sample
    bad = blob[dedf.HEADER_LEN :]
    bad = blob[: dedf.HEADER_LEN] + b"".join(_segments(blob)[:-1])
    with pytest.raises(CryptoError) as e:
        _dec(bad, dek, _with(entry, bad))
    assert e.value.code in ("dedf_tag", "dedf_truncated")


def test_truncation_header_only_and_partial_tag(sample) -> None:
    dek, blob, entry, _ = sample
    for bad in (blob[: dedf.HEADER_LEN], blob[: dedf.HEADER_LEN + 7], blob[:-3]):
        with pytest.raises(CryptoError):
            _dec(bad, dek, _with(entry, bad))


def test_reorder_and_drop_middle(sample) -> None:
    dek, blob, entry, _ = sample
    segs = _segments(blob)
    swapped = blob[: dedf.HEADER_LEN] + b"".join([segs[1], segs[0], *segs[2:]])
    dropped = blob[: dedf.HEADER_LEN] + b"".join([segs[0], *segs[2:]])
    duplicated = blob[: dedf.HEADER_LEN] + b"".join([segs[0], segs[0], *segs[1:]])
    for bad in (swapped, dropped, duplicated):
        with pytest.raises(CryptoError):
            _dec(bad, dek, _with(entry, bad))


def test_extension_after_last(sample) -> None:
    dek, blob, entry, _ = sample
    for bad in (blob + b"\x00", blob + _segments(blob)[-1]):
        with pytest.raises(CryptoError):
            _dec(bad, dek, _with(entry, bad))
        with pytest.raises(CryptoError) as e:
            _dec(bad, dek, entry)  # honest manifest: declared size exceeded
        assert e.value.code in ("dedf_extra_data", "dedf_tag", "dedf_size")


def test_cross_file_splice_and_wrong_key(sample) -> None:
    dek, blob, entry, _ = sample
    other_blob, other_entry = _enc(b"0123456789abcdef" * 20, dek, path="other.csv")
    with pytest.raises(CryptoError):  # per-file key derived from the path
        _dec(
            other_blob,
            dek,
            dedf.FileEntry(entry.path, other_entry.plaintext_size, len(other_blob), other_entry.ciphertext_sha256),
        )
    with pytest.raises(CryptoError):
        _dec(blob, dedf.new_dek(), entry)


def test_hash_and_size_mismatch(sample) -> None:
    dek, blob, entry, _ = sample
    with pytest.raises(CryptoError) as e:
        _dec(blob, dek, dedf.FileEntry(entry.path, entry.plaintext_size, entry.ciphertext_size, "0" * 64))
    assert e.value.code == "dedf_hash"
    with pytest.raises(CryptoError):
        _dec(
            blob,
            dek,
            dedf.FileEntry(entry.path, entry.plaintext_size + 1, entry.ciphertext_size, entry.ciphertext_sha256),
        )
    with pytest.raises(CryptoError):
        _dec(blob, dek, entry, seg=S * 2)  # segment size differs from the manifest


def test_decrypt_to_path_leaves_nothing_on_failure(tmp_path, sample) -> None:
    dek, blob, entry, data = sample
    target = tmp_path / "out" / "f.csv"
    dedf.decrypt_to_path(dek, entry, io.BytesIO(blob), target, segment_size=S)
    assert target.read_bytes() == data
    bad = blob[:-1] + bytes([blob[-1] ^ 1])
    target2 = tmp_path / "out" / "g.csv"
    with pytest.raises(CryptoError):
        dedf.decrypt_to_path(dek, entry, io.BytesIO(bad), target2, segment_size=S)
    assert sorted(p.name for p in target.parent.iterdir()) == ["f.csv"]


@pytest.mark.parametrize("path", ["", "/abs", "a/../b", "./a", "a//b", "a\\b", "a/\x00", "..", "a/"])
def test_bad_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        dedf.validate_relpath(path)


# ---- manifest -------------------------------------------------------------------------------


@pytest.fixture
def dataset(tmp_path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.csv").write_bytes(b"a" * 1000)
    (src / "sub" / "b.bin").write_bytes(b"")
    (src / "skip.txt").write_bytes(b"x")
    kms_key = HpkeKeyPair.generate("bank-a/loans#1")
    owner = EcSigner.generate("k1", "bank-a#k1")
    out = tmp_path / "enc"
    manifest = dedf.encrypt_directory(
        src,
        out,
        dataset_id="bank-a/loans",
        version="v1",
        kms_kid=kms_key.kid,
        kms_public_key=kms_key.public_key,
        signer=owner,
        segment_size=128,
        include=["*.csv", "**/*.bin"],
    )
    return out, manifest, kms_key, owner


def test_encrypt_directory_and_verify(dataset) -> None:
    out, manifest, kms_key, owner = dataset
    assert [f["path"] for f in manifest["files"]] == ["a.csv", "sub/b.bin"]
    raw = (out / "manifest.json").read_bytes()
    jws = (out / "manifest.jws").read_text()
    keys = jwks_lookup([dict(owner.public_jwk(), kid="bank-a#k1")])
    m = dedf.verify_manifest(
        raw, jws, keys, expected_digest=dedf.digest(manifest), expected_dataset_id="bank-a/loans", expected_version="v1"
    )
    dek = dedf.unwrap_dek(m.wrapped_dek(), kms_key.private_key, dataset_id="bank-a/loans", version="v1")
    for f in m.files:
        with (out / dedf.object_key(f.path)).open("rb") as src:
            buf = io.BytesIO()
            dedf.decrypt_stream(dek, f, src, buf, segment_size=m.segment_size)
    with pytest.raises(Exception):
        dedf.unwrap_dek(m.wrapped_dek(), kms_key.private_key, dataset_id="bank-a/loans", version="v2")


def test_manifest_jws_and_digest_checks(dataset) -> None:
    out, manifest, _, owner = dataset
    raw = (out / "manifest.json").read_bytes()
    jws = (out / "manifest.jws").read_text()
    keys = jwks_lookup([dict(owner.public_jwk(), kid="bank-a#k1")])
    with pytest.raises(VerificationError) as e:
        dedf.verify_manifest(raw, jws, keys, expected_digest="sha256:" + "1" * 64)
    assert e.value.code == "manifest_digest"
    tampered = raw.replace(b'"v1"', b'"v2"')
    with pytest.raises(VerificationError):
        dedf.verify_manifest(tampered, jws, keys, expected_digest=None)
    stranger = EcSigner.generate("k1", "bureau#k1")
    forged = dedf.sign_manifest(manifest, stranger)
    with pytest.raises(VerificationError):
        dedf.verify_manifest(
            raw, forged, jwks_lookup([dict(stranger.public_jwk(), kid="bureau#k1")]), expected_digest=None
        )
    with pytest.raises(VerificationError):
        dedf.verify_manifest(raw, jws, keys, expected_digest=None, expected_dataset_id="bank-a/other")


def test_manifest_schema_rejections(dataset) -> None:
    _, manifest, _, _ = dataset
    for mutate in (
        lambda m: m.update(extra=1),
        lambda m: m.update(owner="bureau"),
        lambda m: m["files"].append(dict(m["files"][0])),
        lambda m: m["files"][0].update(path="../x"),
        lambda m: m["files"][0].update(ciphertext_size=m["files"][0]["ciphertext_size"] + 1),
        lambda m: m.update(segment_size=0),
        lambda m: m["wrapped_dek"].update(ct="AAAA"),
        lambda m: m.update(files=[]),
        lambda m: m["files"].append({**m["files"][0], "path": "a.csv/inner"}),
    ):
        broken = {
            **manifest,
            "files": [dict(f) for f in manifest["files"]],
            "wrapped_dek": dict(manifest["wrapped_dek"]),
        }
        mutate(broken)
        with pytest.raises(ValidationError):
            dedf.parse_manifest(broken)


def test_output_manifest_recipients() -> None:
    dek = dedf.new_dek()
    tdc = HpkeKeyPair.generate("h1")
    ds = "tdc/outputs/0b7f0d4e-6a3a-4d6e-9c55-2f1b8a7c1e00/7e3f2b1a-1c2d-4e5f-8a9b-0c1d2e3f4a5b"
    _, entry = _enc(b"model", dek, path="model.json")
    wrapped = dedf.wrap_dek_for_recipients(
        dek, [dedf.Recipient("party:tdc#h1", "h1", tdc.public_key)], dataset_id=ds, version="model"
    )
    m = dedf.build_manifest(
        dataset_id=ds, version="model", owner="tdc", segment_size=S, files=[entry], wrapped_deks=wrapped
    )
    parsed = dedf.parse_manifest(m, output=True)
    got = dedf.unwrap_dek(parsed.wrapped_dek_for("party:tdc#h1"), tdc.private_key, dataset_id=ds, version="model")
    assert got == dek
    with pytest.raises(ValidationError):
        parsed.wrapped_dek_for("party:other#h1")
    with pytest.raises(ValidationError):
        dedf.build_manifest(
            dataset_id=ds, version="model", owner="other", segment_size=S, files=[entry], wrapped_deks=wrapped
        )


def test_symlinks_rejected(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "real").write_text("x")
    (src / "link").symlink_to(src / "real")
    with pytest.raises(ValidationError):
        list(dedf.iter_plain_files(src))
