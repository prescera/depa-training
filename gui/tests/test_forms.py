"""Input parsing without services: policy form, governance records, paths, error mapping."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from depa.errors import NotFoundError, ValidationError
from starlette.datastructures import FormData

from depa_console.errors import explain, redact
from depa_console.forms import empty_target_dir, parse_policy_form, party_record, policy_defaults, release_record

TODAY = date(2026, 11, 23)


def _form(**fields: object) -> FormData:
    items: list[tuple[str, str]] = []
    for key, value in fields.items():
        items.extend((key, v) for v in (value if isinstance(value, list) else [value]))
    return FormData(items)


def test_policy_defaults_are_restrictive() -> None:
    values = policy_defaults(None, ["api://broker"], TODAY)
    assert values["platforms"] == ["aci-snp", "gcp-cs-tdx"] and values["output_modes"] == ["tee-only"]
    assert values["tdcs"] == "" and values["require_dp"] and values["max_runs"] == "1"
    fields = {k: v for k, v in values.items() if not isinstance(v, bool)}
    parsed = parse_policy_form(_form(**fields, require_dp="on"), TODAY)
    assert parsed.errors == {"tdcs": "name at least one consumer party id"}


def test_valid_policy_round_trips() -> None:
    form = _form(platforms=["aci-snp"], tiers=["1", "3"], tdcs="fintech-x, tdc", purposes="TRAINING",
                 require_dp="on", max_epsilon="2", max_delta="1e-6", output_modes=["tdc"],
                 audiences="sts.amazonaws.com", custom_measurements="m1", max_runs="5",
                 not_after="2027-01-31")  # fmt: skip
    parsed = parse_policy_form(form, TODAY)
    assert parsed.errors == {}
    assert parsed.policy == {
        "version": 1,
        "allowed_platforms": ["aci-snp"],
        "allowed_tiers": [1, 3],
        "allowed_tdcs": ["fintech-x", "tdc"],
        "allowed_purposes": ["TRAINING"],
        "privacy": {"require_dp": True, "max_epsilon": 2.0, "max_delta": 1e-6},
        "allowed_output_modes": ["tdc"],
        "allowed_audiences": ["sts.amazonaws.com"],
        "allowed_custom_measurements": ["m1"],
        "max_runs_per_contract": 5,
        "not_after": "2027-01-31T00:00:00Z",
    }
    assert policy_defaults(parsed.policy, [], TODAY)["tdcs"] == "fintech-x, tdc"


def test_policy_rejects_unknown_choices_and_bad_numbers() -> None:
    parsed = parse_policy_form(
        _form(platforms=["sgx"], tiers=["3"], tdcs="tdc", purposes="TRAINING", require_dp="on", max_epsilon="nan",
              max_delta="x", output_modes=["all"], max_runs="ten", not_after="tomorrow"),
        TODAY,
    )  # fmt: skip
    assert set(parsed.errors) == {"platforms", "max_epsilon", "max_delta", "output_modes", "custom_measurements",
                                  "max_runs", "not_after"}  # fmt: skip
    assert parsed.policy is None


def test_governance_records() -> None:
    with pytest.raises(ValidationError):
        party_record('{"party_id": "x"}')
    with pytest.raises(ValidationError):
        release_record('{"release_id": "r"}')
    with pytest.raises(ValidationError):
        party_record('{"a": 1, "a": 2}')  # duplicate keys rejected by the SDK's strict JSON


def test_target_dir(tmp_path: Path) -> None:
    assert empty_target_dir(str(tmp_path / "new"), "destination") == tmp_path / "new"
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "f").write_text("x")
    for bad in ("relative/dir", str(tmp_path / "full"), str(tmp_path / "missing" / "deeper")):
        with pytest.raises(ValidationError):
            empty_target_dir(bad, "destination")


def test_error_mapping_and_redaction() -> None:
    problem = explain(NotFoundError("dataset x not found", service="depa-kms", status=404))
    assert problem.status == 404 and problem.title == "Not found"
    assert redact("token=abc&x=1 eyJhbGciOiJFUzI1NiJ9.eyJzdWIiOiJ4In0.sig") == "token=[redacted]&x=1 [redacted-jwt]"
