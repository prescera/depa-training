"""Runtime guard (audit hook). Each case runs in a fresh interpreter because hooks are irrevocable."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path


def run_guarded(tmp_path: Path, body: str) -> subprocess.CompletedProcess:
    allowed = tmp_path / "allowed"
    allowed.mkdir(exist_ok=True)
    script = textwrap.dedent(
        f"""
        import sys
        from pathlib import Path
        from depa_train.safety import guard
        guard.install(write_roots=[Path({str(allowed)!r})], write_files=[], allow_subprocess=False)
        """
    ) + textwrap.dedent(body)
    return subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=tmp_path, check=False
    )


def test_unpickling_any_class_is_blocked(tmp_path):
    proc = run_guarded(
        tmp_path,
        """
        import pickle, collections
        payload = pickle.dumps(collections.OrderedDict(a=1))
        try:
            pickle.loads(payload)
        except guard.GuardViolation as e:
            print("BLOCKED", e)
        """,
    )
    assert "BLOCKED runtime guard: pickle.find_class is forbidden" in proc.stdout, proc.stderr


def test_malicious_pickle_does_not_execute(tmp_path):
    marker = tmp_path / "pwned"
    proc = run_guarded(
        tmp_path,
        f"""
        import os, pickle
        class Evil:
            def __reduce__(self):
                return (os.system, ("touch {marker}",))
        data = pickle.dumps(Evil())
        try:
            pickle.loads(data)
        except Exception as e:
            print("BLOCKED", type(e).__name__)
        """,
    )
    assert "BLOCKED GuardViolation" in proc.stdout, proc.stderr
    assert not marker.exists()


def test_network_is_blocked(tmp_path):
    proc = run_guarded(
        tmp_path,
        """
        import socket
        attempts = (lambda: socket.create_connection(("127.0.0.1", 9)),
                    lambda: socket.getaddrinfo("example.com", 443))
        for fn in attempts:
            try:
                fn()
            except guard.GuardViolation:
                print("BLOCKED")
        """,
    )
    assert proc.stdout.count("BLOCKED") == 2, proc.stderr


def test_processes_are_blocked(tmp_path):
    proc = run_guarded(
        tmp_path,
        """
        import os, subprocess
        for fn in (lambda: subprocess.run(["true"]), lambda: os.system("true")):
            try:
                fn()
            except guard.GuardViolation:
                print("BLOCKED")
        print(guard.hard_violations())
        """,
    )
    assert proc.stdout.count("BLOCKED") == 2, proc.stderr
    assert "subprocess.Popen: process creation" in proc.stdout


def test_writes_restricted_to_allowed_roots(tmp_path):
    outside = tmp_path / "outside.txt"
    proc = run_guarded(
        tmp_path,
        f"""
        (Path({str(tmp_path / "allowed")!r}) / "ok.txt").write_text("fine")
        try:
            open({str(outside)!r}, "w").write("nope")
        except PermissionError:
            print("WRITE BLOCKED")
        try:
            Path({str(tmp_path / "newdir")!r}).mkdir()
        except PermissionError:
            print("MKDIR BLOCKED")
        print(open({str(tmp_path / "allowed" / "ok.txt")!r}).read())
        """,
    )
    assert "WRITE BLOCKED" in proc.stdout and "MKDIR BLOCKED" in proc.stdout, proc.stderr
    assert "fine" in proc.stdout
    assert not outside.exists()
