from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from depa.errors import StorageError, ValidationError
from depa.storage.factory import party_connector
from depa.storage.file import FileConnector


def test_roundtrip_and_uri(tmp_path: Path) -> None:
    conn = party_connector({"connector": "file", "uri": tmp_path.as_uri() + "/store/"})
    data = os.urandom(20_000)
    conn.upload("objects/a/b.csv.dedf", io.BytesIO(data), len(data))
    with conn.open_read("objects/a/b.csv.dedf") as fh:
        assert fh.read() == data
    assert conn.read_bytes("objects/a/b.csv.dedf", 20_000) == data
    with pytest.raises(StorageError) as exc:
        conn.read_bytes("objects/a/b.csv.dedf", 19_999)
    assert exc.value.code == "too_large"
    assert conn.uri_for("manifest.json") == (tmp_path / "store" / "manifest.json").as_uri()
    assert not [p for p in (tmp_path / "store" / "objects" / "a").iterdir() if p.name.startswith(".")]


def test_missing_object(tmp_path: Path) -> None:
    conn = FileConnector(tmp_path)
    with pytest.raises(StorageError) as exc:
        conn.read_bytes("nope.json", 10)
    assert exc.value.code == "not_found"


@pytest.mark.parametrize("key", ["../x", "/etc/passwd", "a//b", "a/./b", "a\\b", "", "a/../../b"])
def test_key_validation(tmp_path: Path, key: str) -> None:
    conn = FileConnector(tmp_path)
    with pytest.raises(ValidationError):
        conn.read_bytes(key, 10)
    with pytest.raises(ValidationError):
        conn.upload(key, io.BytesIO(b"x"), 1)


def test_symlink_escape_refused(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    root = tmp_path / "root"
    root.mkdir()
    (root / "link.txt").symlink_to(secret)
    (root / "dir").symlink_to(tmp_path)
    conn = FileConnector(root)
    with pytest.raises(StorageError) as exc:
        conn.read_bytes("link.txt", 100)
    assert exc.value.code == "symlink"
    with pytest.raises(StorageError):
        conn.read_bytes("dir/secret.txt", 100)
    with pytest.raises(StorageError):
        conn.upload("dir/new.txt", io.BytesIO(b"x"), 1)
    assert not (tmp_path / "new.txt").exists()


def test_upload_size_mismatch_leaves_nothing(tmp_path: Path) -> None:
    conn = FileConnector(tmp_path)
    with pytest.raises(StorageError) as exc:
        conn.upload("o.bin", io.BytesIO(b"abc"), 5)
    assert exc.value.code == "short_source"
    with pytest.raises(StorageError) as exc:
        conn.upload("o.bin", io.BytesIO(b"abcdef"), 5)
    assert exc.value.code == "long_source"
    assert list(tmp_path.iterdir()) == []
