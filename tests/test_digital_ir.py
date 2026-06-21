"""Tests for ``ltagent.digital_ir`` (Phase 12, Tiny8 CPU Design IR).

Covers:
- Valid Tiny8 CPU IR round-trips through load/dump/validate.
- Bad widths, bad path, unsupported kind, unknown fields rejected
  with stable DESIGN_* error codes.
- Defaults are sane.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ltagent.digital_ir import (
    DESIGN_SCHEMA_VERSION,
    SUPPORTED_DESIGN_KINDS,
    CpuSpec,
    Direction,
    ExpectedState,
    IoPort,
    IoSpec,
    MemorySpec,
    Metadata,
    ProgramSpec,
    VerificationSpec,
    dump_design,
    format_errors,
    load_design,
    validate_dict,
)


def _minimal_ir(**overrides) -> dict:
    base = {
        "schemaVersion": "0.1",
        "domain": "digital",
        "kind": "tiny8_cpu",
        "name": "tiny8_test",
        "description": "tiny8 test design",
        "clock": {"name": "clk", "frequencyHz": 1000000},
        "reset": {"name": "rst", "activeHigh": True, "synchronous": True},
        "cpu": {
            "dataWidth": 8,
            "addressWidth": 8,
            "instructionWidth": 16,
            "architecture": "accumulator",
            "isa": "tiny8_v0",
        },
        "memory": {"romWords": 256, "ramBytes": 256},
        "io": {"ports": []},
        "program": {"source": "demo.asm", "entry": 0, "expectedHaltCyclesMax": 200},
        "verification": {"expected": {"halted": True, "acc": 42, "memory": {}}},
        "artifacts": {"rtl": [], "testbenches": [], "reports": []},
        "metadata": {"createdBy": "ltagent", "source": "test"},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_minimal_ir_validates_and_round_trips() -> None:
    data = _minimal_ir()
    ir = validate_dict(data)
    assert ir.schemaVersion == DESIGN_SCHEMA_VERSION
    assert ir.kind == "tiny8_cpu"
    assert ir.cpu.dataWidth == 8
    assert ir.cpu.addressWidth == 8
    assert ir.cpu.instructionWidth == 16
    # Round-trip via JSON
    text = dump_design(ir)
    reparsed = json.loads(text)
    assert reparsed["kind"] == "tiny8_cpu"
    assert reparsed["cpu"]["dataWidth"] == 8
    # And via dict
    ir2 = validate_dict(reparsed)
    assert ir2.name == ir.name


def test_load_design_from_path(tmp_path: Path) -> None:
    src = tmp_path / "tiny8.design.json"
    src.write_text(json.dumps(_minimal_ir()), encoding="utf-8")
    ir = load_design(src)
    assert ir.name == "tiny8_test"


def test_load_design_accepts_dict() -> None:
    ir = load_design(_minimal_ir())
    assert ir.kind == "tiny8_cpu"


def test_load_design_rejects_wrong_type() -> None:
    with pytest.raises(TypeError):
        load_design(42)  # type: ignore[arg-type]


def test_dump_design_is_json_serialisable() -> None:
    ir = validate_dict(_minimal_ir())
    payload = json.loads(dump_design(ir))
    assert payload["domain"] == "digital"


# ---------------------------------------------------------------------------
# Rejection paths
# ---------------------------------------------------------------------------


def test_unsupported_kind_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        validate_dict(_minimal_ir(kind="arm_cortex_m0"))
    errors = format_errors(exc.value)
    assert any(e.code == "DESIGN_SCHEMA_BAD_KIND" for e in errors)
    assert "tiny8_cpu" in str(exc.value)


def test_unsupported_domain_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        validate_dict(_minimal_ir(domain="analog"))
    errors = format_errors(exc.value)
    assert any(e.code == "DESIGN_DOMAIN_INVALID" for e in errors)


def test_unsupported_schema_version_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        validate_dict(_minimal_ir(schemaVersion="99.0"))
    errors = format_errors(exc.value)
    assert any(e.code == "DESIGN_SCHEMA_UNSUPPORTED_VERSION" for e in errors)


def test_unknown_field_rejected() -> None:
    data = _minimal_ir(unknownField="surprise")
    with pytest.raises(ValidationError) as exc:
        validate_dict(data)
    errors = format_errors(exc.value)
    assert any(e.code.startswith("DESIGN_EXTRA_FIELD") for e in errors)


def test_bad_project_name_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        validate_dict(_minimal_ir(name="UPPER_case_and_space bad"))
    errors = format_errors(exc.value)
    assert any(e.code == "DESIGN_SCHEMA_BAD_NAME" for e in errors)


def test_bad_data_width_rejected() -> None:
    bad_cpu = {
        "dataWidth": 32,
        "addressWidth": 8,
        "instructionWidth": 16,
        "architecture": "accumulator",
        "isa": "tiny8_v0",
    }
    with pytest.raises(ValidationError) as exc:
        validate_dict(_minimal_ir(cpu=bad_cpu))
    errors = format_errors(exc.value)
    assert any(e.code == "DESIGN_CPU_DATA_WIDTH" for e in errors)


def test_bad_isa_widths_rejected() -> None:
    # tiny8_v0 is fixed at 8/8/16; trying 16/16/32 must fail.
    bad_cpu = {
        "dataWidth": 16,
        "addressWidth": 16,
        "instructionWidth": 32,
        "architecture": "accumulator",
        "isa": "tiny8_v0",
    }
    with pytest.raises(ValidationError) as exc:
        validate_dict(_minimal_ir(cpu=bad_cpu))
    assert "isa 'tiny8_v0'" in str(exc.value)


def test_rom_exceeds_address_space_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        validate_dict(
            _minimal_ir(
                cpu={
                    "dataWidth": 8,
                    "addressWidth": 8,
                    "instructionWidth": 16,
                    "architecture": "accumulator",
                    "isa": "tiny8_v0",
                },
                memory={"romWords": 1024, "ramBytes": 256},
            )
        )
    assert "romWords" in str(exc.value)


def test_program_source_must_be_relative_asm() -> None:
    with pytest.raises(ValidationError) as exc:
        validate_dict(_minimal_ir(program={"source": "../escape.asm"}))
    errors = format_errors(exc.value)
    assert any(e.code == "DESIGN_PROG_SOURCE" for e in errors)

    with pytest.raises(ValidationError):
        validate_dict(_minimal_ir(program={"source": "demo.bin"}))


def test_program_entry_negative_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        validate_dict(_minimal_ir(program={"source": "demo.asm", "entry": -1}))
    errors = format_errors(exc.value)
    assert any(e.code == "DESIGN_PROG_ENTRY" for e in errors)


def test_program_halt_cycles_bounded() -> None:
    with pytest.raises(ValidationError) as exc:
        validate_dict(
            _minimal_ir(
                program={
                    "source": "demo.asm",
                    "entry": 0,
                    "expectedHaltCyclesMax": 0,
                }
            )
        )
    errors = format_errors(exc.value)
    assert any(e.code == "DESIGN_PROG_HALT_CYCLES" for e in errors)


def test_verification_acc_bounded() -> None:
    with pytest.raises(ValidationError) as exc:
        validate_dict(
            _minimal_ir(verification={"expected": {"halted": True, "acc": 256, "memory": {}}})
        )
    errors = format_errors(exc.value)
    assert any(e.code == "DESIGN_VERIFY_ACC_RANGE" for e in errors)


def test_verification_memory_key_bounded() -> None:
    # 8-bit address space is 0..255; 99999 is way out.
    bad = _minimal_ir(
        verification={
            "expected": {
                "halted": True,
                "acc": 0,
                "memory": {"99999": 1},
            }
        }
    )
    with pytest.raises(ValidationError) as exc:
        validate_dict(bad)
    assert "out of range" in str(exc.value)


def test_verification_memory_value_bounded() -> None:
    bad = _minimal_ir(
        verification={
            "expected": {
                "halted": True,
                "acc": 0,
                "memory": {"16": 999},
            }
        }
    )
    with pytest.raises(ValidationError) as exc:
        validate_dict(bad)
    assert "must be in 0..255" in str(exc.value)


def test_io_port_duplicate_rejected() -> None:
    ports = [
        {"name": "out0", "direction": "output", "width": 8},
        {"name": "out0", "direction": "output", "width": 8},
    ]
    with pytest.raises(ValidationError) as exc:
        validate_dict(_minimal_ir(io={"ports": ports}))
    errors = format_errors(exc.value)
    assert any(e.code == "DESIGN_IO_PORT_DUPLICATE" for e in errors)


def test_io_port_bad_name_rejected() -> None:
    with pytest.raises(ValidationError):
        validate_dict(
            _minimal_ir(io={"ports": [{"name": "0_bad", "direction": "output", "width": 8}]})
        )


def test_metadata_creator_path_injection_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        validate_dict(_minimal_ir(metadata={"createdBy": "../etc/passwd", "source": "x"}))
    errors = format_errors(exc.value)
    assert any(e.code == "DESIGN_META_CREATOR" for e in errors)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_defaults_are_tiny8_cpu() -> None:
    ir = validate_dict(_minimal_ir())
    assert ir.cpu.architecture == "accumulator"
    assert ir.cpu.isa == "tiny8_v0"
    assert ir.clock.frequencyHz == 1_000_000
    assert ir.reset.synchronous is True
    assert ir.io.ports == []
    assert ir.artifacts.rtl == []


def test_supported_kinds_contains_both_v1_kinds() -> None:
    assert "tiny8_cpu" in SUPPORTED_DESIGN_KINDS
    assert "tiny8_soc" in SUPPORTED_DESIGN_KINDS


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


def test_cpu_spec_standalone_validates() -> None:
    cpu = CpuSpec()
    assert cpu.dataWidth == 8


def test_memory_spec_standalone_validates() -> None:
    mem = MemorySpec()
    assert mem.romWords == 256


def test_io_port_direction_enum_serialises_as_string() -> None:
    port = IoPort(name="out0", direction=Direction.OUTPUT, width=8)
    assert port.direction.value == "output"
    dumped = port.model_dump(mode="json")
    assert dumped["direction"] == "output"


def test_expected_state_halt_default_true() -> None:
    state = ExpectedState()
    assert state.halted is True
    assert state.acc is None
    assert state.memory == {}


def test_expected_state_memory_helper() -> None:
    state = ExpectedState(memory={"16": 42, "32": 99})
    assert state.memory_as_int_dict() == {16: 42, 32: 99}


def test_metadata_default_source() -> None:
    md = Metadata()
    assert md.createdBy == "ltagent"
    assert md.source == "digital_planner"


def test_program_spec_standalone() -> None:
    p = ProgramSpec(source="demo.asm")
    assert p.entry == 0
    assert p.expectedHaltCyclesMax == 200


def test_io_spec_standalone() -> None:
    s = IoSpec(ports=[])
    assert s.ports == []


def test_verification_spec_standalone() -> None:
    v = VerificationSpec()
    assert v.expected.halted is True
