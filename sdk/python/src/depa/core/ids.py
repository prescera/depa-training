"""Identifier grammars from the protocol spec."""

from __future__ import annotations

import re
import uuid

from depa.errors import ValidationError

PARTY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
DATASET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,54}$")  # google.subject <= 127 bytes
KID_RE = re.compile(r"^[A-Za-z0-9._:/#-]{1,200}$")
PLATFORMS = ("aci-snp", "gcp-cs-tdx", "virtual")
PRODUCTION_PLATFORMS = ("aci-snp", "gcp-cs-tdx")
FAMILIES = ("classic", "torch", "llm", "custom")
ROLES = ("tdp", "tdc", "ccrp", "dc-member", "auditor")
KMS_SERVICE = "depa-kms"
CS_SERVICE = "depa-contract-service"


def check_party_id(value: object) -> str:
    if not isinstance(value, str) or not PARTY_ID_RE.fullmatch(value):
        raise ValidationError(f"invalid party_id {value!r}", code="bad_party_id")
    return value


def check_dataset_id(value: object) -> str:
    """``<owner_party_id>/<name>``."""
    if not isinstance(value, str) or value.count("/") != 1:
        raise ValidationError(f"invalid dataset_id {value!r}", code="bad_dataset_id")
    owner, name = value.split("/")
    check_party_id(owner)
    if not DATASET_NAME_RE.fullmatch(name):
        raise ValidationError(f"invalid dataset name in {value!r}", code="bad_dataset_id")
    return value


def dataset_owner(dataset_id: str) -> str:
    return check_dataset_id(dataset_id).split("/", 1)[0]


def check_uuid4(value: object) -> str:
    if not isinstance(value, str):
        raise ValidationError("expected a uuid4 string", code="bad_uuid")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise ValidationError(f"invalid uuid {value!r}", code="bad_uuid") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise ValidationError(f"not a canonical uuid4: {value!r}", code="bad_uuid")
    return value


def check_platform(value: object) -> str:
    if not isinstance(value, str) or value not in PLATFORMS:
        raise ValidationError(f"unknown platform {value!r}", code="bad_platform")
    return value


def parse_key_ref(ref: str) -> tuple[str, str]:
    """``"<party_id>#<kid>"`` -> (party_id, kid)."""
    if not isinstance(ref, str) or ref.count("#") != 1:
        raise ValidationError(f"invalid key reference {ref!r}", code="bad_key_ref")
    party, kid = ref.split("#")
    check_party_id(party)
    if not kid or not KID_RE.fullmatch(kid):
        raise ValidationError(f"invalid kid in {ref!r}", code="bad_key_ref")
    return party, kid


def parse_recipient(value: str) -> tuple[str, str, str]:
    """Output recipient ``party:<id>#<kid>`` or ``kms:<kid>`` -> (kind, party_or_empty, kid)."""
    if isinstance(value, str) and value.startswith("party:"):
        party, kid = parse_key_ref(value[len("party:") :])
        return "party", party, kid
    if isinstance(value, str) and value.startswith("kms:") and KID_RE.fullmatch(value[4:]):
        return "kms", "", value[4:]
    raise ValidationError(f"invalid output recipient {value!r}", code="bad_recipient")
