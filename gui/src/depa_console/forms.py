"""Strict parsing of browser input: release policies, governance proposals, identifiers and paths.

Everything posted is untrusted text. Each parser returns either clean protocol values or
field-level messages; nothing is passed to the SDK unvalidated.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Protocol

from depa.clients.base import Party
from depa.contract.schema import schema_errors
from depa.core.encoding import loads_object
from depa.core.ids import PARTY_ID_RE, PLATFORMS, ROLES
from depa.errors import DepaError, ValidationError

PURPOSE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$")  # contract, run, proposal, release ids
TXID_RE = re.compile(r"^\d+\.\d+$")
OUTPUT_MODES = ("tdc", "tee-only")
MAX_TEXT = 500


class FormLike(Protocol):
    def get(self, key: str, default: Any = None) -> Any: ...

    def getlist(self, key: str) -> list[Any]: ...


def text(form: FormLike, key: str, *, max_len: int = MAX_TEXT) -> str:
    value = form.get(key)
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_len]


def checked(form: FormLike, key: str) -> bool:
    return form.get(key) in ("on", "yes", "true", "1")


def choices(form: FormLike, key: str, allowed: tuple[str, ...]) -> list[str]:
    return [v for v in allowed if v in {x for x in form.getlist(key) if isinstance(x, str)}]


def split_list(value: str) -> list[str]:
    return list(dict.fromkeys(p for p in re.split(r"[,\s]+", value) if p))


def opaque_id(value: str, what: str) -> str:
    if not OPAQUE_ID_RE.fullmatch(value):
        raise ValidationError(f"invalid {what}", code="bad_input")
    return value


# ---------------------------------------------------------------------------------------------
# Release policy


@dataclass
class PolicyForm:
    values: dict[str, Any]
    errors: dict[str, str] = field(default_factory=dict)
    policy: dict[str, Any] | None = None


def policy_defaults(current: Mapping[str, Any] | None, audiences: list[str], today: date) -> dict[str, Any]:
    """Form values: the current policy when there is one, otherwise restrictive defaults."""
    if current is None:
        return {
            "platforms": ["aci-snp", "gcp-cs-tdx"],
            "tiers": ["1"],
            "tdcs": "",
            "purposes": "TRAINING",
            "require_dp": True,
            "max_epsilon": "1.0",
            "max_delta": "1e-05",
            "output_modes": ["tee-only"],
            "audiences": ", ".join(audiences),
            "custom_measurements": "",
            "max_runs": "1",
            "not_after": (today + timedelta(days=90)).isoformat(),
        }
    privacy = current.get("privacy") or {}
    return {
        "platforms": list(current.get("allowed_platforms", [])),
        "tiers": [str(t) for t in current.get("allowed_tiers", [1])],
        "tdcs": ", ".join(current.get("allowed_tdcs", [])),
        "purposes": ", ".join(current.get("allowed_purposes", [])),
        "require_dp": bool(privacy.get("require_dp")),
        "max_epsilon": str(privacy.get("max_epsilon", "")),
        "max_delta": str(privacy.get("max_delta", "")),
        "output_modes": list(current.get("allowed_output_modes", ["tee-only"])),
        "audiences": ", ".join(current.get("allowed_audiences", [])),
        "custom_measurements": ", ".join(current.get("allowed_custom_measurements", [])),
        "max_runs": str(current.get("max_runs_per_contract", "")),
        "not_after": str(current.get("not_after", ""))[:10],
    }


def _number(raw: str, errors: dict[str, str], key: str, *, low: float, high: float, low_open: bool) -> float | None:
    try:
        value = float(raw)
    except ValueError:
        errors[key] = "enter a number"
        return None
    if not math.isfinite(value) or value > high or value < low or (low_open and value == low):
        errors[key] = f"must be {'>' if low_open else '≥'} {low:g} and ≤ {high:g}"
        return None
    return value


def parse_policy_form(form: FormLike, today: date) -> PolicyForm:
    values: dict[str, Any] = {
        "platforms": choices(form, "platforms", PLATFORMS),
        "tiers": choices(form, "tiers", ("1", "2", "3")),
        "tdcs": text(form, "tdcs"),
        "purposes": text(form, "purposes"),
        "require_dp": checked(form, "require_dp"),
        "max_epsilon": text(form, "max_epsilon", max_len=40),
        "max_delta": text(form, "max_delta", max_len=40),
        "output_modes": choices(form, "output_modes", OUTPUT_MODES),
        "audiences": text(form, "audiences", max_len=4000),
        "custom_measurements": text(form, "custom_measurements", max_len=4000),
        "max_runs": text(form, "max_runs", max_len=10),
        "not_after": text(form, "not_after", max_len=10),
        "confirm_virtual": checked(form, "confirm_virtual"),
        "confirm_any_tdc": checked(form, "confirm_any_tdc"),
    }
    out = PolicyForm(values)
    errors = out.errors
    if not values["platforms"]:
        errors["platforms"] = "allow at least one platform"
    elif "virtual" in values["platforms"] and not values["confirm_virtual"]:
        errors["platforms"] = "'virtual' has no hardware protection; confirm it is for development data only"
    tiers = [int(t) for t in values["tiers"]]
    if not tiers:
        errors["tiers"] = "allow at least one tier"
    tdcs = split_list(values["tdcs"])
    bad = [t for t in tdcs if t != "*" and not PARTY_ID_RE.fullmatch(t)]
    if not tdcs:
        errors["tdcs"] = "name at least one consumer party id"
    elif bad:
        errors["tdcs"] = f"not party ids: {', '.join(bad)}"
    elif "*" in tdcs and not values["confirm_any_tdc"]:
        errors["tdcs"] = "'*' allows every consumer; confirm it explicitly"
    purposes = split_list(values["purposes"])
    bad = [p for p in purposes if not PURPOSE_RE.fullmatch(p)]
    if not purposes:
        errors["purposes"] = "name at least one purpose (e.g. TRAINING)"
    elif bad:
        errors["purposes"] = f"purposes are UPPER_CASE identifiers: {', '.join(bad)}"
    privacy: dict[str, Any] = {"require_dp": values["require_dp"]}
    if values["require_dp"]:
        eps = _number(values["max_epsilon"], errors, "max_epsilon", low=0, high=100, low_open=True)
        delta = _number(values["max_delta"], errors, "max_delta", low=0, high=0.1, low_open=False)
        if eps is not None and delta is not None:
            privacy.update(max_epsilon=eps, max_delta=delta)
    modes = values["output_modes"]
    if not modes:
        errors["output_modes"] = "allow at least one output mode"
    audiences = split_list(values["audiences"])
    if any(len(a) > 512 for a in audiences):
        errors["audiences"] = "audiences are at most 512 characters"
    measurements = split_list(values["custom_measurements"])
    if any(len(m) > 200 for m in measurements):
        errors["custom_measurements"] = "measurements are at most 200 characters"
    elif 3 in tiers and not measurements:
        errors["custom_measurements"] = "tier 3 needs at least one approved custom measurement"
    max_runs = int(values["max_runs"]) if values["max_runs"].isdigit() else 0
    if not 1 <= max_runs <= 10000:
        errors["max_runs"] = "a whole number from 1 to 10000"
    try:
        not_after = date.fromisoformat(values["not_after"])
        if not today < not_after <= today + timedelta(days=3650):
            errors["not_after"] = "a date after today and within ten years"
    except ValueError:
        errors["not_after"] = "a date (YYYY-MM-DD)"
    if errors:
        return out
    policy: dict[str, Any] = {
        "version": 1,
        "allowed_platforms": values["platforms"],
        "allowed_tiers": tiers,
        "allowed_tdcs": tdcs,
        "allowed_purposes": purposes,
        "privacy": privacy,
        "allowed_output_modes": modes,
        "allowed_audiences": audiences,
        "allowed_custom_measurements": measurements,
        "max_runs_per_contract": max_runs,
        "not_after": f"{values['not_after']}T00:00:00Z",
    }
    schema = schema_errors(policy, "release-policy-v1")
    if schema:
        errors["_form"] = "; ".join(schema)
        return out
    out.policy = policy
    return out


# ---------------------------------------------------------------------------------------------
# Governance


def party_record(raw: str) -> dict[str, Any]:
    """A pasted ``party.json`` (public record from ``depa party keygen``), fully checked."""
    try:
        record = loads_object(raw)
        party = Party.parse(record)
    except (DepaError, ValueError) as exc:
        raise ValidationError(f"not a valid party record: {exc}", code="bad_party") from exc
    if not party.roles or any(r not in ROLES for r in party.roles):
        raise ValidationError(f"roles must be a non-empty subset of {', '.join(ROLES)}", code="bad_party")
    if not party.signing_keys or not party.hpke_public_keys:
        raise ValidationError("the record needs signing_keys and hpke_public_keys", code="bad_party")
    return record


def release_record(raw: str) -> dict[str, Any]:
    """A pasted release record (spec §6) or release manifest ``{release, deployment}``."""
    try:
        doc = loads_object(raw)
    except (DepaError, ValueError) as exc:
        raise ValidationError(f"not JSON: {exc}", code="bad_release") from exc
    record = doc.get("release") if "deployment" in doc else doc
    errors = schema_errors(record, "ccr-release")
    if not isinstance(record, dict) or errors:
        raise ValidationError("not a valid CCR release record: " + "; ".join(errors), code="bad_release")
    return record


def expiry_seconds(form: FormLike) -> int:
    raw = text(form, "expires_days", max_len=3)
    days = int(raw) if raw.isdigit() else 0
    if not 1 <= days <= 30:
        raise ValidationError("proposal lifetime must be 1 to 30 days", code="bad_input")
    return days * 86400


# ---------------------------------------------------------------------------------------------
# Local paths


def existing_dir(raw: str, what: str) -> Path:
    path = Path(raw).expanduser()
    if not raw or not path.is_absolute() or not path.is_dir():
        raise ValidationError(f"{what} must be an existing directory given as an absolute path", code="bad_path")
    return path.resolve()


def empty_target_dir(raw: str, what: str) -> Path:
    path = Path(raw).expanduser()
    if not raw or not path.is_absolute():
        raise ValidationError(f"{what} must be an absolute path", code="bad_path")
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValidationError(f"{what} must not exist yet or be an empty directory", code="bad_path")
    if not path.parent.is_dir():
        raise ValidationError(f"the parent of {what} does not exist", code="bad_path")
    return path.resolve()
