"""Validation tests for Circuit IR.

Each invalid fixture maps to a single validation rule from plan section
10.3. We assert that the loader rejects the fixture AND surfaces a
structured IRError with the expected stable error code.
"""

from __future__ import annotations

import json

import jsonschema
import pytest
from pydantic import ValidationError

from ltagent.ir import load_ir, validate_dict
from tests._testdata import EXAMPLES, EXAMPLES_DIR, INVALID_DIR, INVALID_FIXTURES


def _load_fixture_dict(name: str) -> dict:
    return json.loads((INVALID_DIR / f"{name}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Per-rule rejection tests (one per invalid fixture).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture_name,expected_code",
    sorted(INVALID_FIXTURES.items()),
    ids=sorted(INVALID_FIXTURES.keys()),
)
def test_invalid_fixture_rejected_with_expected_code(
    fixture_name: str, expected_code: str
) -> None:
    """Each fixture must trigger its declared error code."""
    data = _load_fixture_dict(fixture_name)
    ir, errs = validate_dict(data)
    assert ir is None, f"{fixture_name} unexpectedly accepted"
    codes = [e.code for e in errs]
    assert expected_code in codes, (
        f"{fixture_name}: expected code {expected_code!r}, got {codes}"
    )


# ---------------------------------------------------------------------------
# Specific high-priority rules get extra assertions.
# ---------------------------------------------------------------------------


def test_missing_ground_fails_root_level_check() -> None:
    data = _load_fixture_dict("missing_ground")
    with pytest.raises(ValidationError):
        load_ir(data)


def test_duplicate_component_id_reports_specific_code() -> None:
    """The error path must point at the second occurrence."""
    data = _load_fixture_dict("duplicate_component_id")
    _, errs = validate_dict(data)
    dup_errors = [e for e in errs if e.code == "COMP_DUPLICATE_ID"]
    assert dup_errors, "expected at least one COMP_DUPLICATE_ID error"
    # Path should mention 'components' (the offending list).
    assert any("components" in e.path for e in dup_errors)


def test_unsafe_directive_rejected() -> None:
    """`.include /etc/passwd` must be rejected by allowlist."""
    data = _load_fixture_dict("unsafe_directive")
    _, errs = validate_dict(data)
    assert any(e.code == "DIR_UNSUPPORTED" for e in errs)


def test_unknown_node_reports_node_name() -> None:
    """The unknown-node error should mention the bad node in detail."""
    data = _load_fixture_dict("unknown_node")
    _, errs = validate_dict(data)
    node_errs = [e for e in errs if e.code == "COMP_UNKNOWN_NODE"]
    assert node_errs
    assert any("GHOST" in e.detail for e in node_errs)


def test_wrong_arity_rejects_component() -> None:
    """Resistor with 3 nodes must fail."""
    data = _load_fixture_dict("wrong_arity")
    _, errs = validate_dict(data)
    assert any(e.code == "COMP_WRONG_ARITY" for e in errs)


def test_mismatched_prefix_rejects_component() -> None:
    """Resistor with spicePrefix=V must fail."""
    data = _load_fixture_dict("mismatched_prefix")
    _, errs = validate_dict(data)
    assert any(e.code == "COMP_PREFIX_MISMATCH" for e in errs)


def test_extra_fields_rejected_at_root() -> None:
    """`extra='forbid'` ensures typos in IR fields fail loudly."""
    data = _load_fixture_dict("unknown_field")
    _, errs = validate_dict(data)
    extra = [e for e in errs if e.code.startswith("EXTRA_FIELD_AT_")]
    assert extra
    assert any("SNEAKYEXTRAFIELD" in e.code for e in extra)


# ---------------------------------------------------------------------------
# Valid examples must pass the JSON Schema (insofar as the schema covers
# the rule; for strict cross-field rules, validate_dict is the truth).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("example_name", EXAMPLES)
def test_valid_example_passes_json_schema(
    example_name: str, json_schema: dict
) -> None:
    """Each valid example must satisfy the JSON Schema.

    Note: jsonschema does not enforce pydantic's custom field_validators
    (e.g. arity, ground presence), so we also call load_ir as the source
    of truth.
    """
    data = json.loads((EXAMPLES_DIR / f"{example_name}.ir.json").read_text())
    jsonschema.validate(instance=data, schema=json_schema)
    # Also re-load via pydantic to confirm cross-field rules pass.
    ir = load_ir(data)
    assert ir.topology == example_name
