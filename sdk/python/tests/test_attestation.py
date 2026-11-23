from __future__ import annotations

import base64
import ctypes
import json
import os
import socketserver
import struct
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

import httpx
import pytest

from depa.attestation import get_provider
from depa.attestation import snp as snpmod
from depa.attestation.gcp import GCA_ISSUER, GcpConfidentialSpaceProvider
from depa.attestation.snp import SnpProvider, fetch_snp_report
from depa.attestation.virtual import VirtualProvider
from depa.errors import AttestationError

BH = bytes(range(32))
PEM = "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n"
TCB = 0xDB18000000000004


def make_report(report_data: bytes, tcb: int = TCB) -> bytes:
    report = bytearray(snpmod.REPORT_SIZE)
    report[0x50 : 0x50 + 64] = report_data
    struct.pack_into("<Q", report, 0x180, tcb)
    return bytes(report)


# --------------------------------------------------------------------------- ioctl layer


class FakeDevice:
    def __init__(self, device: str, *, status: int = 0, echo: bool = True) -> None:
        self.device = device
        self.status = status
        self.echo = echo
        self.calls: list[tuple[int, bytes]] = []
        self.request: bytes = b""

    def exists(self, path: str) -> bool:
        return path == self.device

    def open(self, path: str) -> int:
        assert path == self.device
        return os.open(os.devnull, os.O_RDONLY)

    def ioctl(self, fd: int, code: int, payload: bytearray) -> int:
        self.calls.append((code, bytes(payload)))
        if self.device == snpmod.SEV_DEVICE_6:
            assert len(payload) == 32
            version, req_ptr, rsp_ptr, fw_err = struct.unpack("<B7xQQQ", payload)
            assert (version, fw_err) == (1, 0)
        else:
            assert len(payload) == 40
            fields = struct.unpack("<BBBBHHQH6xQII", payload)
            req_type, rsp_type, version, zero, req_len, z2, req_ptr, rsp_len, rsp_ptr, z3, z4 = fields
            assert (req_type, rsp_type, version, zero, req_len, z2, rsp_len, z3, z4) == (
                5,
                6,
                1,
                0,
                96,
                0,
                1280,
                0,
                0,
            )
        self.request = ctypes.string_at(req_ptr, 96)
        data = self.request[:64] if self.echo else b"\xff" * 64
        response = struct.pack("<I", self.status) + b"\x00" * 28 + make_report(data)
        ctypes.memmove(rsp_ptr, response, len(response))
        return 0


@pytest.mark.parametrize(
    ("device", "code"),
    [
        (snpmod.SEV_DEVICE_6, snpmod.SNP_GET_REPORT_IOCTL_6),
        (snpmod.SEV_DEVICE_5, snpmod.SNP_GET_REPORT_IOCTL_5),
    ],
)
def test_fetch_report_struct_layout(device: str, code: int) -> None:
    dev = FakeDevice(device)
    report_data = BH + b"\x00" * 32
    report = fetch_snp_report(report_data, ioctl=dev.ioctl, device_exists=dev.exists, open_device=dev.open)
    assert len(report) == 1184
    assert report[0x50 : 0x50 + 64] == report_data
    assert dev.calls[0][0] == code
    assert dev.request == report_data + b"\x00" * 32
    assert snpmod.reported_tcb(report) == TCB


def test_payload_builders_exact_bytes() -> None:
    v6 = snpmod.build_payload_v6(0x1122334455667788, 0x99AABBCCDDEEFF00)
    assert (
        bytes(v6)
        == b"\x01"
        + b"\x00" * 7
        + (0x1122334455667788).to_bytes(8, "little")
        + (0x99AABBCCDDEEFF00).to_bytes(8, "little")
        + b"\x00" * 8
    )
    v5 = snpmod.build_payload_v5(0x10, 0x20)
    assert bytes(v5[:8]) == bytes([5, 6, 1, 0, 96, 0, 0, 0])
    assert int.from_bytes(v5[8:16], "little") == 0x10
    assert int.from_bytes(v5[16:18], "little") == 1280
    assert bytes(v5[18:24]) == b"\x00" * 6
    assert int.from_bytes(v5[24:32], "little") == 0x20
    assert bytes(v5[32:40]) == b"\x00" * 8


def test_fetch_report_firmware_status_error() -> None:
    dev = FakeDevice(snpmod.SEV_DEVICE_6, status=0x16)
    with pytest.raises(AttestationError, match="status"):
        fetch_snp_report(b"\x00" * 64, ioctl=dev.ioctl, device_exists=dev.exists, open_device=dev.open)


def test_fetch_report_no_device() -> None:
    with pytest.raises(AttestationError) as err:
        fetch_snp_report(b"\x00" * 64, device_exists=lambda _: False)
    assert err.value.code == "no_snp_device"


def test_fetch_report_rejects_bad_report_data_length() -> None:
    with pytest.raises(AttestationError):
        fetch_snp_report(b"\x00" * 32)


# --------------------------------------------------------------------------- SNP provider


def write_context(directory: Path, tcbm: str = "DB18000000000004", reference: bytes = b"\xd2\x84cose") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    host = {"vcekCert": PEM, "certificateChain": PEM + PEM, "tcbm": tcbm, "cacheControl": "86400"}
    (directory / "host-amd-cert-base64").write_text(base64.b64encode(json.dumps(host).encode()).decode())
    (directory / "reference-info-base64").write_text(base64.b64encode(reference).decode() + "\n")
    return directory


def echo_fetcher(report_data: bytes) -> bytes:
    return make_report(report_data)


def test_snp_provider_envelope(tmp_path: Path) -> None:
    ctx = write_context(tmp_path / "security-context-1")
    provider = SnpProvider(report_fetcher=echo_fetcher, security_context_dir=ctx)
    ev = provider.evidence(BH, "https://cs.example")
    env = ev.envelope
    assert set(env) == {"platform", "evidence", "endorsements", "uvm_endorsements", "endorsed_tcb"}
    assert env["platform"] == "aci-snp"
    report = base64.b64decode(env["evidence"])
    assert report[0x50:0x70] == BH and report[0x70:0x90] == b"\x00" * 32
    assert base64.b64decode(env["endorsements"]).decode() == PEM + PEM + PEM
    assert base64.b64decode(env["uvm_endorsements"]) == b"\xd2\x84cose"
    assert env["endorsed_tcb"] == "db18000000000004"
    assert ev.bearer_token is None
    # cached per binding hash, audience-independent
    assert provider.evidence(BH, "https://kms.example") is ev


def test_snp_provider_rejects_non_echoing_report(tmp_path: Path) -> None:
    ctx = write_context(tmp_path / "ctx")
    provider = SnpProvider(report_fetcher=lambda rd: make_report(b"\x01" * 64), security_context_dir=ctx)
    with pytest.raises(AttestationError, match="REPORT_DATA"):
        provider.evidence(BH, "https://cs.example")


def test_snp_provider_rejects_short_report(tmp_path: Path) -> None:
    ctx = write_context(tmp_path / "ctx")
    provider = SnpProvider(report_fetcher=lambda rd: b"\x00" * 100, security_context_dir=ctx)
    with pytest.raises(AttestationError, match="length"):
        provider.evidence(BH, "https://cs.example")


def test_snp_provider_bad_binding_hash(tmp_path: Path) -> None:
    provider = SnpProvider(report_fetcher=echo_fetcher, security_context_dir=tmp_path)
    with pytest.raises(AttestationError):
        provider.evidence(b"\x00" * 31, "https://cs.example")


def test_security_context_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UVM_SECURITY_CONTEXT_DIR", raising=False)
    with pytest.raises(AttestationError):
        snpmod.find_security_context_dir(tmp_path)
    write_context(tmp_path / "security-context-abc")
    assert snpmod.find_security_context_dir(tmp_path) == tmp_path / "security-context-abc"
    monkeypatch.setenv("UVM_SECURITY_CONTEXT_DIR", "/elsewhere")
    assert snpmod.find_security_context_dir(tmp_path) == Path("/elsewhere")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: (d / "host-amd-cert-base64").write_text("not base64!!"),
        lambda d: (d / "host-amd-cert-base64").write_text(base64.b64encode(b'{"vcekCert": 1}').decode()),
        lambda d: (d / "host-amd-cert-base64").write_text(base64.b64encode(b"[]").decode()),
        lambda d: (d / "reference-info-base64").unlink(),
        lambda d: (d / "reference-info-base64").write_text(""),
    ],
)
def test_security_context_malformed(tmp_path: Path, mutate: Any) -> None:
    ctx = write_context(tmp_path / "ctx")
    mutate(ctx)
    with pytest.raises(AttestationError):
        snpmod.read_security_context(ctx)


def thim_transport(tcbm: str, calls: list[httpx.Request], status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        body = {"vcekCert": PEM, "certificateChain": PEM, "tcbm": tcbm, "cacheControl": "1"}
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


def test_tcbm_mismatch_uses_thim(tmp_path: Path) -> None:
    ctx = write_context(tmp_path / "ctx", tcbm="0300000000000001")
    calls: list[httpx.Request] = []
    provider = SnpProvider(
        report_fetcher=echo_fetcher,
        security_context_dir=ctx,
        thim_transport=thim_transport("DB18000000000004", calls),
    )
    env = provider.evidence(BH, "https://cs.example").envelope
    assert len(calls) == 1
    assert calls[0].headers["Metadata"] == "true"
    assert str(calls[0].url) == snpmod.THIM_URL
    assert env["endorsed_tcb"] == "db18000000000004"
    assert base64.b64decode(env["endorsements"]).decode() == PEM + PEM


def test_tcbm_mismatch_thim_still_stale_fails_closed(tmp_path: Path) -> None:
    ctx = write_context(tmp_path / "ctx", tcbm="0300000000000001")
    calls: list[httpx.Request] = []
    provider = SnpProvider(
        report_fetcher=echo_fetcher,
        security_context_dir=ctx,
        thim_transport=thim_transport("0300000000000001", calls),
    )
    with pytest.raises(AttestationError) as err:
        provider.evidence(BH, "https://cs.example")
    assert err.value.code == "tcb_mismatch"


def test_tcbm_mismatch_thim_unavailable(tmp_path: Path) -> None:
    ctx = write_context(tmp_path / "ctx", tcbm="0300000000000001")
    calls: list[httpx.Request] = []
    provider = SnpProvider(
        report_fetcher=echo_fetcher,
        security_context_dir=ctx,
        thim_transport=thim_transport("DB18000000000004", calls, status=500),
    )
    with pytest.raises(AttestationError) as err:
        provider.evidence(BH, "https://cs.example")
    assert err.value.code == "thim"


# --------------------------------------------------------------------------- GCP provider


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_jwt(claims: dict[str, Any]) -> str:
    header = b64url(json.dumps({"alg": "RS256", "kid": "k", "typ": "JWT"}).encode())
    return f"{header}.{b64url(json.dumps(claims).encode())}.{b64url(b'sig')}"


class TokenServer:
    def __init__(self, socket_path: str) -> None:
        self.requests: list[dict[str, Any]] = []
        self.paths: list[str] = []
        self.claims_override: dict[str, Any] = {}
        self.exp = 2_000_000_000
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers["Content-Length"])
                req = json.loads(self.rfile.read(length))
                outer.requests.append(req)
                outer.paths.append(self.path)
                claims = {
                    "iss": GCA_ISSUER,
                    "aud": req["audience"],
                    "eat_nonce": req["nonces"][0],
                    "exp": outer.exp,
                    "iat": 1_000,
                    "hwmodel": "GCP_INTEL_TDX",
                }
                claims.update(outer.claims_override)
                body = make_jwt(claims).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def address_string(self) -> str:
                return "unix"

            def log_message(self, format: str, *args: Any) -> None:
                return

        class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

        self.server = Server(socket_path, Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def token_server(tmp_path: Path) -> Iterator[tuple[TokenServer, str]]:
    path = str(tmp_path / "teeserver.sock")
    assert len(path) < 100
    server = TokenServer(path)
    try:
        yield server, path
    finally:
        server.close()


def test_gcp_token_request_and_cache(token_server: tuple[TokenServer, str]) -> None:
    server, path = token_server
    now = [1_000.0]
    provider = GcpConfidentialSpaceProvider(socket_path=path, clock=lambda: now[0])
    ev = provider.evidence(BH, "https://cs.example/app")
    assert ev.envelope == {"platform": "gcp-cs-tdx"}
    assert ev.bearer_token is not None and ev.bearer_token.count(".") == 2
    assert server.requests == [{"audience": "https://cs.example/app", "token_type": "OIDC", "nonces": [BH.hex()]}]
    assert server.paths == ["/v1/token"]
    # cached for the same audience, separate token for another audience
    assert provider.evidence(BH, "https://cs.example/app").bearer_token == ev.bearer_token
    assert len(server.requests) == 1
    provider.evidence(BH, "https://kms.example/app")
    assert len(server.requests) == 2
    # refreshed once within 60 s of expiry
    now[0] = server.exp - 30
    server.exp = now[0] + 3600
    provider.evidence(BH, "https://cs.example/app")
    assert len(server.requests) == 3


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"iss": "https://evil.example"}, "issuer"),
        ({"aud": "https://other.example"}, "audience"),
        ({"eat_nonce": ["00" * 32]}, "nonce"),
        ({"exp": "soon"}, "exp"),
        ({"exp": 1_010}, "expired"),
    ],
)
def test_gcp_token_sanity_checks(token_server: tuple[TokenServer, str], override: dict[str, Any], match: str) -> None:
    server, path = token_server
    server.claims_override = override
    provider = GcpConfidentialSpaceProvider(socket_path=path, clock=lambda: 1_000.0)
    with pytest.raises(AttestationError, match=match):
        provider.evidence(BH, "https://cs.example")


def test_gcp_nonce_list_accepted(token_server: tuple[TokenServer, str]) -> None:
    server, path = token_server
    server.claims_override = {"eat_nonce": ["ab" * 32, BH.hex()], "aud": ["https://cs.example", "x"]}
    provider = GcpConfidentialSpaceProvider(socket_path=path, clock=lambda: 1_000.0)
    assert provider.evidence(BH, "https://cs.example").bearer_token


def test_gcp_socket_missing(tmp_path: Path) -> None:
    provider = GcpConfidentialSpaceProvider(socket_path=str(tmp_path / "missing.sock"), timeout_s=2)
    with pytest.raises(AttestationError) as err:
        provider.evidence(BH, "https://cs.example")
    assert err.value.code == "teeserver"


def test_gcp_http_error() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(503, text="no"))
    provider = GcpConfidentialSpaceProvider(transport=transport)
    with pytest.raises(AttestationError, match="503"):
        provider.evidence(BH, "https://cs.example")


def test_gcp_rejects_bad_inputs() -> None:
    provider = GcpConfidentialSpaceProvider(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(AttestationError):
        provider.evidence(b"short", "https://cs.example")
    with pytest.raises(AttestationError):
        provider.evidence(BH, "http://cs.example")


# --------------------------------------------------------------------------- virtual + factory


def test_virtual_envelope() -> None:
    ev = VirtualProvider().evidence(BH, "https://cs.example")
    assert ev.envelope == {"platform": "virtual", "binding_hash": BH.hex(), "virtual_id": "virtual-dev"}
    assert ev.bearer_token is None
    with pytest.raises(AttestationError):
        VirtualProvider().evidence(b"\x00" * 33, "https://cs.example")


def test_get_provider() -> None:
    assert get_provider("virtual").platform == "virtual"
    assert get_provider("aci-snp").platform == "aci-snp"
    assert get_provider("gcp-cs-tdx").platform == "gcp-cs-tdx"
