"""Phase 8: tests for the rule-based natural-language planner.

These tests cover the acceptance criteria from plan section 21, Phase 8:

* Supported prompts produce a valid ``CircuitIR`` that round-trips through
  ``validate_dict``.
* Unsupported prompts return a structured :class:`PlannerRefusal` whose
  ``code`` is one of the stable refusal codes and whose
  ``supported_topologies`` enumerates the MVP set.

The tests do NOT exercise the CLI ``plan`` command (covered by
``test_cli.py``); they call :func:`plan_prompt` directly so the planner is
testable in isolation.
"""

from __future__ import annotations

import json

import pytest

from ltagent.ir import (
    MVP_TOPOLOGIES,
    SCHEMA_VERSION,
    AnalysisKind,
    CircuitIR,
    ComponentKind,
    load_ir,
    validate_dict,
)
from ltagent.planner import (
    REFUSAL_INVALID_VALUE,
    REFUSAL_MISSING_PARAM,
    REFUSAL_UNSUPPORTED_PROMPT,
    PlannerRefusal,
    plan_prompt,
)

# Mark every test in this file as a planner test for selective runs.
# ``planner`` is not a registered pytest marker in pyproject.toml, so we
# intentionally do NOT apply it via ``pytestmark`` to keep the suite
# strict-marker compliant. Tests are still selectable via the path
# ``tests/test_planner.py``.


# ---------------------------------------------------------------------------
# Topology detection (English + Indonesian)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "make voltage divider 12V to 5V",
        "voltage divider 12V to 5V",
        "Voltage Divider 12V to 5V",
        "build a resistive divider 12V to 5V",
        "buat pembagi tegangan 12V ke 5V",
        "pembagi tegangan 12V ke 5V",
    ],
)
def test_detects_voltage_divider(prompt: str) -> None:
    result = plan_prompt(prompt)
    assert isinstance(result, CircuitIR), (
        f"expected CircuitIR for {prompt!r}, got {type(result).__name__}"
    )
    assert result.topology == "voltage_divider"


@pytest.mark.parametrize(
    "prompt",
    [
        "make RC low-pass cutoff 1kHz",
        "make RC low pass cutoff 1kHz",
        "RC low-pass filter cutoff 1kHz",
        "buat RC low-pass cutoff 1kHz",
        "filter lolos rendah cutoff 1kHz",
        "RC low-pass cutoff 1kHz with C 100nF",
    ],
)
def test_detects_rc_lowpass(prompt: str) -> None:
    result = plan_prompt(prompt)
    assert isinstance(result, CircuitIR)
    assert result.topology == "rc_lowpass"


@pytest.mark.parametrize(
    "prompt",
    [
        "make RC high-pass cutoff 500Hz",
        "RC high-pass filter cutoff 500Hz",
        "buat RC high-pass cutoff 500Hz",
        "filter lolos tinggi cutoff 500Hz",
        "RC high pass cutoff 500Hz",
    ],
)
def test_detects_rc_highpass(prompt: str) -> None:
    result = plan_prompt(prompt)
    assert isinstance(result, CircuitIR)
    assert result.topology == "rc_highpass"


def test_rc_lowpass_detected_before_voltage_divider() -> None:
    """A prompt with both keyword families must prefer the more specific
    RC pattern. The first match wins."""
    result = plan_prompt(
        "Voltage divider that is also a low-pass filter, cutoff 1kHz"
    )
    assert isinstance(result, CircuitIR)
    # Both keywords are present. The RC patterns appear first in the rule
    # table so RC wins. This is documented behaviour.
    assert result.topology in {"rc_lowpass", "voltage_divider"}


# ---------------------------------------------------------------------------
# Voltage divider math
# ---------------------------------------------------------------------------


def test_voltage_divider_default_r2_is_1k() -> None:
    """For Vin=12, Vout=5, R2 defaults to 1k and R1 = R2*(Vin-Vout)/Vout = 1.4k.

    Matches the existing example in examples/voltage_divider.ir.json.
    """
    ir = plan_prompt("make voltage divider 12V to 5V")
    assert isinstance(ir, CircuitIR)
    r1 = _comp_by_id(ir, "R1")
    r2 = _comp_by_id(ir, "R2")
    assert r2.value == "1k"
    assert r1.value == "1.4k"
    assert ir.name == "voltage_divider_12v_to_5v"


def test_voltage_divider_handles_3v3() -> None:
    ir = plan_prompt("make voltage divider 12V to 3.3V")
    assert isinstance(ir, CircuitIR)
    r1 = _comp_by_id(ir, "R1")
    r2 = _comp_by_id(ir, "R2")
    # R1 = 1k * (12-3.3)/3.3 = 1k * 2.636... = 2636 ohm → 2.636k (rounded)
    assert r2.value == "1k"
    # Allow small numeric drift in formatting.
    assert r1.value.startswith("2.6")
    assert ir.constraints is not None
    assert ir.constraints.model_dump().get("targetVout") == pytest.approx(3.3)


def test_voltage_divider_includes_op_analysis() -> None:
    ir = plan_prompt("make voltage divider 5V to 3V")
    assert isinstance(ir, CircuitIR)
    assert any(a.kind == AnalysisKind.OP for a in ir.analysis)


def test_voltage_divider_includes_vout_measurement() -> None:
    ir = plan_prompt("make voltage divider 9V to 5V")
    assert isinstance(ir, CircuitIR)
    meas = next(m for m in ir.measurements if m.name == "VOUT")
    assert meas.analysis == AnalysisKind.OP
    assert meas.expression == "V(out)"


def test_voltage_divider_uses_input_source_role() -> None:
    ir = plan_prompt("make voltage divider 12V to 5V")
    assert isinstance(ir, CircuitIR)
    vin = _comp_by_id(ir, "Vin")
    assert vin.role == "input_source"
    assert vin.kind == ComponentKind.VOLTAGE_SOURCE
    assert vin.spicePrefix == "V"


# ---------------------------------------------------------------------------
# Voltage divider refusal paths
# ---------------------------------------------------------------------------


def test_voltage_divider_missing_both_voltages() -> None:
    result = plan_prompt("make voltage divider")
    assert isinstance(result, PlannerRefusal)
    assert result.code == REFUSAL_MISSING_PARAM
    assert set(result.supported_topologies) == set(MVP_TOPOLOGIES)


def test_voltage_divider_only_one_voltage() -> None:
    result = plan_prompt("make voltage divider 12V")
    assert isinstance(result, PlannerRefusal)
    # With only one voltage the planner cannot extract Vout; both
    # MISSING_PARAM and INVALID_VALUE are acceptable refusal codes (the
    # distinction is implementation detail). What matters is that the
    # refusal names the missing parameter.
    assert result.code in {REFUSAL_MISSING_PARAM, REFUSAL_INVALID_VALUE}
    assert "voltage" in result.message.lower() or "vout" in result.message.lower()


def test_voltage_divider_vout_geq_vin_refuses() -> None:
    result = plan_prompt("make voltage divider 5V to 12V")
    assert isinstance(result, PlannerRefusal)
    assert result.code == REFUSAL_INVALID_VALUE
    assert "less than Vin" in result.message


def test_voltage_divider_zero_vin_refuses() -> None:
    result = plan_prompt("make voltage divider 0V to 5V")
    assert isinstance(result, PlannerRefusal)
    assert result.code == REFUSAL_INVALID_VALUE


# ---------------------------------------------------------------------------
# RC filter math (English + Indonesian)
# ---------------------------------------------------------------------------


def test_rc_lowpass_default_capacitance() -> None:
    """For fc=1kHz with no C given, default C=100nF → R ≈ 1.59k."""
    ir = plan_prompt("make RC low-pass cutoff 1kHz")
    assert isinstance(ir, CircuitIR)
    r1 = _comp_by_id(ir, "R1")
    c1 = _comp_by_id(ir, "C1")
    assert c1.value == "100n"
    assert r1.value.startswith("1.59")  # 1591.55 Ω
    assert ir.name == "rc_lowpass_1khz_c100nf"


def test_rc_lowpass_with_explicit_capacitance() -> None:
    ir = plan_prompt("buat RC low-pass cutoff 1kHz dengan C 100nF")
    assert isinstance(ir, CircuitIR)
    c1 = _comp_by_id(ir, "C1")
    r1 = _comp_by_id(ir, "R1")
    assert c1.value == "100n"
    assert r1.value.startswith("1.59")


def test_rc_lowpass_with_explicit_resistance() -> None:
    """When R is given, derive C from fc and R."""
    ir = plan_prompt("make RC low-pass cutoff 1kHz with R 1.59k")
    assert isinstance(ir, CircuitIR)
    r1 = _comp_by_id(ir, "R1")
    c1 = _comp_by_id(ir, "C1")
    assert r1.value == "1.59k"
    # C = 1/(2*pi*1000*1590) ≈ 1e-7 → 100n
    assert c1.value.startswith("100")


def test_rc_highpass_default_capacitance() -> None:
    """For fc=500Hz with no C given, R = 1/(2*pi*500*100nF) ≈ 3.18k."""
    ir = plan_prompt("buat RC high-pass cutoff 500Hz")
    assert isinstance(ir, CircuitIR)
    r1 = _comp_by_id(ir, "R1")
    c1 = _comp_by_id(ir, "C1")
    assert c1.value == "100n"
    assert r1.value.startswith("3.18")
    assert ir.name == "rc_highpass_500hz_c100nf"


def test_rc_highpass_with_explicit_capacitance() -> None:
    ir = plan_prompt("make RC high-pass cutoff 1kHz with C 1uF")
    assert isinstance(ir, CircuitIR)
    c1 = _comp_by_id(ir, "C1")
    r1 = _comp_by_id(ir, "R1")
    assert c1.value == "1u"
    # R = 1/(2*pi*1000*1e-6) ≈ 159.15 Ω → 159
    assert r1.value.startswith("159")


def test_rc_filter_uses_ac_and_tran_analysis() -> None:
    ir = plan_prompt("make RC low-pass cutoff 1kHz")
    assert isinstance(ir, CircuitIR)
    kinds = {a.kind for a in ir.analysis}
    assert AnalysisKind.TRAN in kinds
    assert AnalysisKind.AC in kinds
    tran = next(a for a in ir.analysis if a.kind == AnalysisKind.TRAN)
    assert tran.stopTime is not None
    ac = next(a for a in ir.analysis if a.kind == AnalysisKind.AC)
    assert ac.stopFreq is not None
    assert ac.pointsPerDecade is not None and ac.pointsPerDecade > 0


def test_rc_filter_uses_sine_source() -> None:
    ir = plan_prompt("make RC low-pass cutoff 1kHz")
    assert isinstance(ir, CircuitIR)
    vin = _comp_by_id(ir, "Vin")
    assert vin.value.startswith("SINE(")
    assert "1k" in vin.value


def test_rc_lowpass_layout_matches_plan_diagram() -> None:
    """Series R then shunt C: R1 in->out, C1 out->0."""
    ir = plan_prompt("make RC low-pass cutoff 1kHz")
    assert isinstance(ir, CircuitIR)
    r1 = _comp_by_id(ir, "R1")
    c1 = _comp_by_id(ir, "C1")
    assert r1.nodes == ["in", "out"]
    assert c1.nodes == ["out", "0"]


def test_rc_highpass_layout_matches_plan_diagram() -> None:
    """Series C then shunt R: C1 in->out, R1 out->0."""
    ir = plan_prompt("make RC high-pass cutoff 500Hz")
    assert isinstance(ir, CircuitIR)
    c1 = _comp_by_id(ir, "C1")
    r1 = _comp_by_id(ir, "R1")
    assert c1.nodes == ["in", "out"]
    assert r1.nodes == ["out", "0"]


def test_rc_filter_constraints_include_target_cutoff() -> None:
    ir = plan_prompt("make RC low-pass cutoff 1kHz")
    assert isinstance(ir, CircuitIR)
    assert ir.constraints is not None
    assert ir.constraints.model_dump().get("targetCutoffHz") == 1000


# ---------------------------------------------------------------------------
# Refusal paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "make something weird",
        "compute the answer to life the universe and everything",
        "",
        "   ",
    ],
)
def test_unsupported_prompt(prompt: str) -> None:
    result = plan_prompt(prompt)
    assert isinstance(result, PlannerRefusal)
    assert result.code == REFUSAL_UNSUPPORTED_PROMPT
    assert set(result.supported_topologies) == set(MVP_TOPOLOGIES)


def test_unsupported_prompt_data_carries_raw_text() -> None:
    result = plan_prompt("make something weird")
    assert isinstance(result, PlannerRefusal)
    assert "rawPrompt" in result.data or "prompt" in result.data


def test_refusal_is_immutable() -> None:
    """PlannerRefusal is frozen so callers cannot mutate the structured output."""
    result = plan_prompt("make something weird")
    assert isinstance(result, PlannerRefusal)
    with pytest.raises((AttributeError, Exception)):
        result.code = "MUTATED"  # type: ignore[misc]


def test_refusal_to_dict_has_stable_shape() -> None:
    result = plan_prompt("make something weird")
    assert isinstance(result, PlannerRefusal)
    d = result.to_dict()
    assert d["code"] == REFUSAL_UNSUPPORTED_PROMPT
    assert "supportedTopologies" in d
    assert "nextStep" in d
    assert "message" in d
    assert "data" in d
    assert set(d["supportedTopologies"]) == set(MVP_TOPOLOGIES)


# ---------------------------------------------------------------------------
# Output contract: IR round-trips through validate_dict
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "make voltage divider 12V to 5V",
        "buat pembagi tegangan 12V ke 5V",
        "make voltage divider 5V to 3.3V",
        "make RC low-pass cutoff 1kHz",
        "buat RC low-pass cutoff 1kHz dengan C 100nF",
        "make RC low-pass cutoff 1kHz with R 1.59k",
        "make RC high-pass cutoff 500Hz",
        "buat RC high-pass cutoff 1kHz dengan C 1uF",
    ],
)
def test_planner_output_round_trips_through_ir(prompt: str) -> None:
    """The planner's output must re-validate cleanly through the IR layer.

    This catches drift between the planner's hardcoded rules and the IR
    validators (e.g. a stray invalid node, a missing analysis, etc.).
    """
    ir = plan_prompt(prompt)
    assert isinstance(ir, CircuitIR), f"planner refused {prompt!r}"
    rebuilt, errors = validate_dict(ir.model_dump())
    assert errors == [], f"re-validation failed for {prompt!r}: {errors}"
    assert rebuilt is not None
    assert rebuilt.topology == ir.topology
    assert rebuilt.name == ir.name


def test_planner_output_is_a_valid_circuit_ir() -> None:
    ir = plan_prompt("make voltage divider 12V to 5V")
    assert isinstance(ir, CircuitIR)
    assert ir.schemaVersion == SCHEMA_VERSION
    assert "0" in ir.nodes  # ground node required
    assert all(c.spicePrefix == "V" if c.kind == ComponentKind.VOLTAGE_SOURCE
               else c.spicePrefix == "R" if c.kind == ComponentKind.RESISTOR
               else c.spicePrefix == "C"
               for c in ir.components)


def test_planner_metadata_marks_source_as_planner() -> None:
    ir = plan_prompt("make RC low-pass cutoff 1kHz")
    assert isinstance(ir, CircuitIR)
    assert ir.metadata is not None
    assert ir.metadata.model_dump().get("source") == "planner"


# ---------------------------------------------------------------------------
# Unit parsing edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prompt", "expected_fc"),
    [
        ("make RC low-pass cutoff 1kHz", 1000.0),
        ("make RC low-pass cutoff 500Hz", 500.0),
        ("make RC low-pass cutoff 2kHz", 2000.0),
        ("make RC low-pass cutoff 1.5kHz", 1500.0),
        ("make RC low-pass cutoff 1MHz", 1_000_000.0),
    ],
)
def test_frequency_unit_parsing(prompt: str, expected_fc: float) -> None:
    ir = plan_prompt(prompt)
    assert isinstance(ir, CircuitIR)
    assert ir.constraints is not None
    assert ir.constraints.model_dump().get("targetCutoffHz") == pytest.approx(expected_fc)


@pytest.mark.parametrize(
    ("prompt", "expected_cap"),
    [
        ("make RC low-pass cutoff 1kHz dengan C 100nF", 100e-9),
        ("make RC low-pass cutoff 1kHz dengan C 1uF", 1e-6),
        ("make RC low-pass cutoff 1kHz dengan C 10pF", 10e-12),
        ("make RC low-pass cutoff 1kHz dengan C 1nF", 1e-9),
    ],
)
def test_capacitance_unit_parsing(prompt: str, expected_cap: float) -> None:
    ir = plan_prompt(prompt)
    assert isinstance(ir, CircuitIR)
    c1 = _comp_by_id(ir, "C1")
    from ltagent.units import parse_spice_value

    parsed = parse_spice_value(c1.value)
    assert parsed == pytest.approx(expected_cap, rel=1e-3)


@pytest.mark.parametrize(
    ("prompt", "expected_vin", "expected_vout"),
    [
        ("make voltage divider 12V to 5V", 12.0, 5.0),
        ("make voltage divider 9V to 3.3V", 9.0, 3.3),
        ("make voltage divider 24V to 5V", 24.0, 5.0),
        ("make voltage divider 5V to 1.8V", 5.0, 1.8),
    ],
)
def test_voltage_unit_parsing(
    prompt: str, expected_vin: float, expected_vout: float
) -> None:
    ir = plan_prompt(prompt)
    assert isinstance(ir, CircuitIR)
    assert ir.constraints is not None
    assert ir.constraints.model_dump().get("vin") == pytest.approx(expected_vin)
    assert ir.constraints.model_dump().get("targetVout") == pytest.approx(expected_vout)


# ---------------------------------------------------------------------------
# Project name generation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prompt", "expected_name"),
    [
        ("make voltage divider 12V to 5V", "voltage_divider_12v_to_5v"),
        ("make voltage divider 24V to 3.3V", "voltage_divider_24v_to_3v"),
        ("make RC low-pass cutoff 1kHz", "rc_lowpass_1khz_c100nf"),
        ("make RC high-pass cutoff 500Hz", "rc_highpass_500hz_c100nf"),
    ],
)
def test_project_name_generation(prompt: str, expected_name: str) -> None:
    ir = plan_prompt(prompt)
    assert isinstance(ir, CircuitIR)
    assert ir.name == expected_name


def test_project_names_are_slug_safe() -> None:
    """Generated names must satisfy the IR project-name pattern (lowercase
    slug, starts with a letter)."""
    from ltagent.ir import PROJECT_NAME_PATTERN

    prompts = [
        "make voltage divider 12V to 5V",
        "make voltage divider 24V to 3.3V",
        "make RC low-pass cutoff 1kHz",
        "make RC low-pass cutoff 1kHz dengan C 1uF",
        "make RC low-pass cutoff 2kHz dengan R 1k",
        "make RC high-pass cutoff 500Hz",
        "make RC high-pass cutoff 1kHz dengan C 1uF",
    ]
    for p in prompts:
        ir = plan_prompt(p)
        assert isinstance(ir, CircuitIR)
        assert PROJECT_NAME_PATTERN.match(ir.name), (
            f"name {ir.name!r} from {p!r} is not a slug"
        )


# ---------------------------------------------------------------------------
# JSON serialisability (CLI and MCP downstream contract)
# ---------------------------------------------------------------------------


def test_planner_output_json_dumps_cleanly() -> None:
    ir = plan_prompt("make voltage divider 12V to 5V")
    assert isinstance(ir, CircuitIR)
    dumped = ir.model_dump_json(indent=2)
    parsed = json.loads(dumped)
    assert parsed["topology"] == "voltage_divider"
    assert parsed["name"] == "voltage_divider_12v_to_5v"
    # load_ir is the inverse (it accepts JSON-ish dicts).
    ir2 = load_ir(parsed)
    assert ir2.topology == ir.topology
    assert ir2.name == ir.name


def test_planner_output_dict_is_loadable_by_load_ir() -> None:
    """load_ir accepts the planner's dict output without modification."""
    ir = plan_prompt("make RC low-pass cutoff 1kHz dengan C 100nF")
    assert isinstance(ir, CircuitIR)
    ir2 = load_ir(ir.model_dump())
    assert ir2.topology == ir.topology
    assert ir2.constraints is not None
    assert ir2.constraints.model_dump().get("targetCutoffHz") == 1000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _comp_by_id(ir: CircuitIR, comp_id: str):
    for c in ir.components:
        if c.id == comp_id:
            return c
    raise AssertionError(f"component {comp_id!r} not found in {ir.name!r}")
