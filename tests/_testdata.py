"""Path constants and fixture mapping for tests.

Kept as a regular module so test files can import it directly. pytest's
conftest.py handles the auto-discovered fixtures (see conftest.py).
"""

from __future__ import annotations

from pathlib import Path

PHASE1_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = PHASE1_ROOT / "examples"
INVALID_DIR = PHASE1_ROOT / "tests" / "fixtures" / "invalid"
SCHEMA_PATH = PHASE1_ROOT / "schemas" / "circuit_ir.schema.json"

EXAMPLES: list[str] = ["voltage_divider", "rc_lowpass", "rc_highpass"]

INVALID_FIXTURES: dict[str, str] = {
    "missing_ground": "NODES_MISSING_GROUND",
    "duplicate_component_id": "COMP_DUPLICATE_ID",
    "bad_topology": "SCHEMA_BAD_TOPOLOGY",
    "unknown_node": "COMP_UNKNOWN_NODE",
    "wrong_arity": "COMP_WRONG_ARITY",
    "mismatched_prefix": "COMP_PREFIX_MISMATCH",
    "bad_name": "SCHEMA_BAD_NAME",
    "bad_schema_version": "SCHEMA_UNSUPPORTED_VERSION",
    "unsafe_directive": "DIR_UNSUPPORTED",
    "bad_measurement": "MEAS_UNKNOWN_ANALYSIS",
    "empty_components": "COMP_MISSING",
    "source_value_missing": "COMP_SOURCE_VALUE_REQUIRED",
    "missing_analysis": "ANALYSIS_MISSING",
    "unknown_field": "EXTRA_FIELD_AT_SNEAKYEXTRAFIELD",
    "bad_probe": "PROBE_INVALID",
}
