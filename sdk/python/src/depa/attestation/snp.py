"""``aci-snp`` attestation: AMD SEV-SNP evidence on Azure Confidential ACI (spec §9.2).

How evidence is collected (implemented directly in Python; the upstream ``get-snp-report``
tool from microsoft/confidential-sidecar-containers is not bundled):

* **Report**: an ``SNP_GET_REPORT`` ioctl on the guest device with ``REPORT_DATA[0..32] =
  binding_hash`` and ``REPORT_DATA[32..64] = 0``, VMPL 0. Two kernel ABIs exist and both are
  supported with the exact request layouts of the upstream sidecar
  (``pkg/attest/attestation_report_fetcher_linux.go``): ``/dev/sev`` (5.15 Mariner UVM, 40-byte
  ``sev_snp_guest_request`` payload) and ``/dev/sev-guest`` (Linux 6.x, 32-byte
  ``snp_guest_request_ioctl``). ``/dev/sev`` is preferred when both exist, as upstream does.
  The 1184-byte report is taken from offset 32 of ``MSG_REPORT_RSP``; we check firmware status,
  the length and that the report echoes the requested ``REPORT_DATA``.
* **Endorsements**: C-ACI places a security-context directory in the container
  (``$UVM_SECURITY_CONTEXT_DIR``, else ``/security-context-*``) containing
  ``host-amd-cert-base64`` (base64 JSON ``{vcekCert, certificateChain, tcbm, cacheControl}``
  supplied by the host from THIM) and ``reference-info-base64`` (base64 COSE_Sign1 UVM
  endorsement). ``endorsements`` = ``vcekCert + certificateChain`` (PEM: VCEK, ASK, ARK),
  ``endorsed_tcb`` = ``tcbm``, ``uvm_endorsements`` = the raw COSE_Sign1 bytes. These are the
  inputs CCF's ``snp_attestation.verifySnpAttestation`` expects.
* **Stale host certificates**: if ``tcbm`` differs from the report's ``REPORTED_TCB`` (offset
  0x180) — e.g. after a host TCB update — fresh certificates are fetched from the local THIM
  endpoint ``http://169.254.169.254/metadata/THIM/amd/certification`` (header
  ``Metadata: true``). If they still do not match we fail closed instead of sending
  endorsements the verifier would reject.

Residual items: the AMD KDS / Azure cert-cache fallbacks of the upstream sidecar are not
implemented (THIM is the supported source on C-ACI); the env-var fallback
(``UVM_HOST_AMD_CERTIFICATE`` / ``UVM_REFERENCE_INFO``) is intentionally not used because the
container environment is operator-controlled. None of this is security-relevant for the
verifier (the services verify everything), only for liveness.
"""

from __future__ import annotations

import contextlib
import ctypes
import fcntl
import os
import struct
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from depa.attestation import Evidence
from depa.attestation.virtual import check_binding_hash
from depa.core.encoding import b64_decode, b64_encode, loads_object
from depa.errors import AttestationError, ValidationError

REPORT_SIZE = 1184
REPORT_DATA_OFFSET = 0x50
REPORT_DATA_SIZE = 64
REPORTED_TCB_OFFSET = 0x180
REPORT_REQ_SIZE = 96
REPORT_RSP_SIZE = 1280
REPORT_RSP_CONTAINER_SIZE_6 = 4000
REPORT_OFFSET_IN_RSP = 32
MSG_REPORT_REQ = 5
MSG_REPORT_RSP = 6

SEV_DEVICE_5 = "/dev/sev"
SEV_DEVICE_6 = "/dev/sev-guest"
SNP_GET_REPORT_IOCTL_5 = 3223868161
SNP_GET_REPORT_IOCTL_6 = 3223343872

HOST_AMD_CERT_FILE = "host-amd-cert-base64"
REFERENCE_INFO_FILE = "reference-info-base64"
THIM_URL = "http://169.254.169.254/metadata/THIM/amd/certification"
MAX_THIM_BYTES = 1 << 20

IoctlFn = Callable[[int, int, bytearray], Any]
ReportFetcher = Callable[[bytes], bytes]


# ---------------------------------------------------------------------------------------------
# Report fetching (ioctl)


def build_payload_v5(req_ptr: int, rsp_ptr: int) -> bytearray:
    """``struct sev_snp_guest_request`` of the 5.15 ``/dev/sev`` driver (40 bytes, SysV AMD64 ABI)."""
    payload = struct.pack(
        "<BBBBHHQH6xQII",
        MSG_REPORT_REQ,
        MSG_REPORT_RSP,
        1,
        0,
        REPORT_REQ_SIZE,
        0,
        req_ptr,
        REPORT_RSP_SIZE,
        rsp_ptr,
        0,
        0,
    )
    return bytearray(payload)


def build_payload_v6(req_ptr: int, rsp_ptr: int) -> bytearray:
    """``struct snp_guest_request_ioctl`` of the Linux 6.x ``/dev/sev-guest`` driver (32 bytes)."""
    payload = struct.pack("<B7xQQQ", 1, req_ptr, rsp_ptr, 0)
    return bytearray(payload)


def fetch_snp_report(
    report_data: bytes,
    *,
    ioctl: IoctlFn = fcntl.ioctl,
    device_exists: Callable[[str], bool] = os.path.exists,
    open_device: Callable[[str], int] | None = None,
) -> bytes:
    """Request an SNP attestation report with the given 64-byte ``REPORT_DATA``."""
    if len(report_data) != REPORT_DATA_SIZE:
        raise AttestationError("REPORT_DATA must be 64 bytes", code="bad_report_data")
    if device_exists(SEV_DEVICE_5):
        device, code, rsp_size, builder = (
            SEV_DEVICE_5,
            SNP_GET_REPORT_IOCTL_5,
            REPORT_RSP_SIZE,
            build_payload_v5,
        )
    elif device_exists(SEV_DEVICE_6):
        device, code, rsp_size, builder = (
            SEV_DEVICE_6,
            SNP_GET_REPORT_IOCTL_6,
            REPORT_RSP_CONTAINER_SIZE_6,
            build_payload_v6,
        )
    else:
        raise AttestationError("no SEV-SNP guest device (/dev/sev, /dev/sev-guest)", code="no_snp_device")
    opener = open_device or (lambda path: os.open(path, os.O_RDWR | os.O_CLOEXEC))
    req = ctypes.create_string_buffer(report_data.ljust(REPORT_REQ_SIZE, b"\x00"), REPORT_REQ_SIZE)
    rsp = ctypes.create_string_buffer(rsp_size)
    payload = builder(ctypes.addressof(req), ctypes.addressof(rsp))
    try:
        fd = opener(device)
    except OSError as exc:
        raise AttestationError(f"cannot open {device}: {exc.strerror}", code="snp_device") from exc
    try:
        ioctl(fd, code, payload)
    except OSError as exc:
        raise AttestationError(f"SNP_GET_REPORT ioctl failed: {exc.strerror}", code="snp_ioctl") from exc
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)
    raw = rsp.raw
    (status,) = struct.unpack_from("<I", raw, 0)
    if status != 0:
        raise AttestationError(f"SNP firmware returned status {status}", code="snp_status")
    return raw[REPORT_OFFSET_IN_RSP : REPORT_OFFSET_IN_RSP + REPORT_SIZE]


def reported_tcb(report: bytes) -> int:
    (value,) = struct.unpack_from("<Q", report, REPORTED_TCB_OFFSET)
    return int(value)


# ---------------------------------------------------------------------------------------------
# Endorsements (security context / THIM)


@dataclass(frozen=True)
class HostCerts:
    vcek_cert: str
    certificate_chain: str
    tcbm: int

    @property
    def endorsements_pem(self) -> bytes:
        return (self.vcek_cert + self.certificate_chain).encode("ascii")

    @classmethod
    def parse(cls, obj: object) -> HostCerts:
        if not isinstance(obj, dict):
            raise AttestationError("host AMD certificate document is not an object", code="bad_host_certs")
        vcek, chain, tcbm = obj.get("vcekCert"), obj.get("certificateChain"), obj.get("tcbm")
        if not isinstance(vcek, str) or "BEGIN CERTIFICATE" not in vcek:
            raise AttestationError("host AMD certificates lack vcekCert", code="bad_host_certs")
        if not isinstance(chain, str) or "BEGIN CERTIFICATE" not in chain:
            raise AttestationError("host AMD certificates lack certificateChain", code="bad_host_certs")
        if not isinstance(tcbm, str) or not 1 <= len(tcbm) <= 16:
            raise AttestationError("host AMD certificates lack a valid tcbm", code="bad_host_certs")
        try:
            value = int(tcbm, 16)
        except ValueError as exc:
            raise AttestationError("host AMD certificates tcbm is not hex", code="bad_host_certs") from exc
        try:
            vcek.encode("ascii")
            chain.encode("ascii")
        except UnicodeEncodeError as exc:
            raise AttestationError("host AMD certificates are not ASCII PEM", code="bad_host_certs") from exc
        return cls(vcek, chain, value)


def find_security_context_dir(root: Path = Path("/")) -> Path:
    env = os.environ.get("UVM_SECURITY_CONTEXT_DIR")
    if env:
        return Path(env)
    try:
        candidates = sorted(p for p in root.iterdir() if p.name.startswith("security-context-") and p.is_dir())
    except OSError as exc:
        raise AttestationError("cannot scan for the UVM security context", code="no_security_context") from exc
    if not candidates:
        raise AttestationError(
            "UVM security context not found (set UVM_SECURITY_CONTEXT_DIR or run on Confidential ACI)",
            code="no_security_context",
        )
    return candidates[0]


def _read_b64_file(path: Path) -> bytes:
    try:
        text = path.read_text("ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise AttestationError(
            f"cannot read {path.name} from the security context", code="no_security_context"
        ) from exc
    try:
        data = b64_decode(text)
    except ValidationError as exc:
        raise AttestationError(f"{path.name} is not valid base64", code="bad_security_context") from exc
    if not data:
        raise AttestationError(f"{path.name} is empty", code="bad_security_context")
    return data


def read_security_context(directory: Path) -> tuple[HostCerts, bytes]:
    """Return ``(host certificates, raw UVM endorsement COSE_Sign1 bytes)``."""
    host_raw = _read_b64_file(directory / HOST_AMD_CERT_FILE)
    try:
        host = HostCerts.parse(loads_object(host_raw))
    except ValidationError as exc:
        raise AttestationError("host-amd-cert-base64 is not a JSON object", code="bad_host_certs") from exc
    uvm = _read_b64_file(directory / REFERENCE_INFO_FILE)
    return host, uvm


# ---------------------------------------------------------------------------------------------
# Provider


class SnpProvider:
    platform = "aci-snp"

    def __init__(
        self,
        *,
        report_fetcher: ReportFetcher | None = None,
        security_context_dir: Path | None = None,
        thim_url: str = THIM_URL,
        thim_transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._fetch = report_fetcher or fetch_snp_report
        self._context_dir = security_context_dir
        self._thim_url = thim_url
        self._thim_transport = thim_transport
        self._cache: dict[bytes, Evidence] = {}

    def _fetch_thim(self) -> HostCerts:
        try:
            with httpx.Client(timeout=10.0, transport=self._thim_transport, follow_redirects=False) as client:
                response = client.get(self._thim_url, headers={"Metadata": "true"})
        except httpx.HTTPError as exc:
            raise AttestationError(f"THIM certificate fetch failed: {type(exc).__name__}", code="thim") from exc
        if response.status_code != 200 or len(response.content) > MAX_THIM_BYTES:
            raise AttestationError(f"THIM certificate fetch failed: HTTP {response.status_code}", code="thim")
        try:
            return HostCerts.parse(loads_object(response.content))
        except ValidationError as exc:
            raise AttestationError("THIM returned malformed certificates", code="thim") from exc

    def evidence(self, binding_hash: bytes, audience: str) -> Evidence:
        digest = check_binding_hash(binding_hash)
        cached = self._cache.get(digest)
        if cached is not None:
            return cached
        report_data = digest + b"\x00" * 32
        report = self._fetch(report_data)
        if len(report) != REPORT_SIZE:
            raise AttestationError("SNP report has the wrong length", code="bad_snp_report")
        if report[REPORT_DATA_OFFSET : REPORT_DATA_OFFSET + REPORT_DATA_SIZE] != report_data:
            raise AttestationError("SNP report does not echo the requested REPORT_DATA", code="bad_snp_report")
        tcb = reported_tcb(report)
        context_dir = self._context_dir or find_security_context_dir()
        host, uvm_endorsements = read_security_context(context_dir)
        if host.tcbm != tcb:
            host = self._fetch_thim()
            if host.tcbm != tcb:
                raise AttestationError(
                    f"host AMD certificates (tcbm {host.tcbm:016x}) do not match the report's REPORTED_TCB {tcb:016x}",
                    code="tcb_mismatch",
                )
        evidence = Evidence(
            envelope={
                "platform": "aci-snp",
                "evidence": b64_encode(report),
                "endorsements": b64_encode(host.endorsements_pem),
                "uvm_endorsements": b64_encode(uvm_endorsements),
                "endorsed_tcb": f"{host.tcbm:016x}",
            }
        )
        self._cache[digest] = evidence
        return evidence
