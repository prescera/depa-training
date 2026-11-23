"""``virtual`` attestation (development only, spec §1 / §9.2).

Unsigned: services accept it only when governance sets ``allow_virtual_attestation`` and the
dataset policy lists ``virtual``. Never enable for production datasets.
"""

from __future__ import annotations

from depa.attestation import Evidence
from depa.errors import AttestationError

VIRTUAL_ID = "virtual-dev"


def check_binding_hash(binding_hash: bytes) -> bytes:
    if not isinstance(binding_hash, bytes | bytearray) or len(binding_hash) != 32:
        raise AttestationError("binding_hash must be exactly 32 bytes", code="bad_binding_hash")
    return bytes(binding_hash)


class VirtualProvider:
    platform = "virtual"

    def __init__(self, *, virtual_id: str = VIRTUAL_ID) -> None:
        self._virtual_id = virtual_id

    def evidence(self, binding_hash: bytes, audience: str) -> Evidence:
        digest = check_binding_hash(binding_hash)
        return Evidence(envelope={"platform": "virtual", "binding_hash": digest.hex(), "virtual_id": self._virtual_id})
