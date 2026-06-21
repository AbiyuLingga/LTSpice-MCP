"""Tests for ``ltagent.digital_generator`` (Phase 12, Tiny8).

Covers:
- File list produced matches the plan doc.
- Determinism: two runs with the same input produce byte-identical
  files.
- Path safety: a request that would escape the project dir is
  rejected.
- Manifest shape matches the plan doc.
- Generated Verilog is non-empty and contains the expected module
  names.
- The testbench contains the expected verification tokens.
"""

from __future__ import annotations

from pathlib import Path

from ltagent.digital_generator import (
    ALL_RTL_PATHS,
    ALL_TESTBENCH_PATHS,
    PATH_DESIGN_IR,
    PATH_MANIFEST,
    PATH_PROGRAM_ASM,
    PATH_PROGRAM_MEM,
    PATH_RTL_ALU,
    PATH_RTL_CONTROL,
    PATH_RTL_CPU,
    PATH_RTL_RAM,
    PATH_RTL_ROM,
    PATH_RTL_TOP,
    PATH_SPICE_COMPANION,
    PATH_TB_TOP,
    generate_project,
    render_manifest,
    render_testbench,
    render_top,
)
from ltagent.digital_ir import (
    CpuSpec,
    DesignIR,
    ExpectedState,
    IoSpec,
    MemorySpec,
    Metadata,
    ProgramSpec,
    VerificationSpec,
)


def _minimal_ir() -> DesignIR:
    return DesignIR(
        schemaVersion="0.1",
        domain="digital",
        kind="tiny8_cpu",
        name="tiny8_test",
        description="test",
        cpu=CpuSpec(),
        memory=MemorySpec(),
        io=IoSpec(ports=[]),
        program=ProgramSpec(source="demo.asm", entry=0, expectedHaltCyclesMax=200),
        verification=VerificationSpec(
            expected=ExpectedState(halted=True, acc=42, memory={"16": 20, "17": 42}),
        ),
        metadata=Metadata(),
    )


# ---------------------------------------------------------------------------
# File list
# ---------------------------------------------------------------------------


def test_generate_project_creates_full_file_set(tmp_path: Path) -> None:
    project = generate_project(_minimal_ir(), tmp_path)
    written_paths = {f.relative_path for f in project.files}
    expected = {
        PATH_DESIGN_IR,
        PATH_MANIFEST,
        PATH_RTL_ALU,
        PATH_RTL_CONTROL,
        PATH_RTL_ROM,
        PATH_RTL_RAM,
        PATH_RTL_CPU,
        PATH_RTL_TOP,
        PATH_TB_TOP,
        PATH_PROGRAM_ASM,
        PATH_PROGRAM_MEM,
        PATH_SPICE_COMPANION,
    }
    assert expected.issubset(written_paths)


def test_all_rtl_paths_covered() -> None:
    assert PATH_RTL_ALU in ALL_RTL_PATHS
    assert PATH_RTL_CONTROL in ALL_RTL_PATHS
    assert PATH_RTL_ROM in ALL_RTL_PATHS
    assert PATH_RTL_RAM in ALL_RTL_PATHS
    assert PATH_RTL_CPU in ALL_RTL_PATHS
    assert PATH_RTL_TOP in ALL_RTL_PATHS


def test_all_testbench_paths_covered() -> None:
    assert PATH_TB_TOP in ALL_TESTBENCH_PATHS


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_generate_project_is_deterministic(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    pa = generate_project(_minimal_ir(), a)
    pb = generate_project(_minimal_ir(), b)
    by_path_a = {f.relative_path: f.sha256_short for f in pa.files}
    by_path_b = {f.relative_path: f.sha256_short for f in pb.files}
    assert by_path_a == by_path_b
    # And the actual bytes match.
    for rel in by_path_a:
        assert (a / rel).read_bytes() == (b / rel).read_bytes()


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def test_generated_files_stay_under_project_dir(tmp_path: Path) -> None:
    project = generate_project(_minimal_ir(), tmp_path)
    project_dir = project.project_dir.resolve()
    for f in project.files:
        full = (project_dir / f.relative_path).resolve()
        # Resolved path is under project_dir.
        full.relative_to(project_dir)


# ---------------------------------------------------------------------------
# Manifest shape
# ---------------------------------------------------------------------------


def test_manifest_contains_required_keys() -> None:
    import json

    ir = _minimal_ir()
    text = render_manifest(ir)
    payload = json.loads(text)
    assert payload["schemaVersion"] == "0.1"
    assert payload["projectKind"] == "digital"
    assert payload["designKind"] == "tiny8_cpu"
    assert payload["topModule"] == "tiny8_top"
    assert payload["sourceIr"] == PATH_DESIGN_IR
    assert PATH_RTL_ALU in payload["rtlFiles"]
    assert PATH_TB_TOP in payload["testbenches"]
    assert PATH_PROGRAM_ASM in payload["programs"]


# ---------------------------------------------------------------------------
# Verilog content
# ---------------------------------------------------------------------------


def test_rtl_top_contains_tiny8_top_module() -> None:
    body = render_top(_minimal_ir())
    assert "module tiny8_top" in body
    assert "tiny8_rom" in body
    assert "tiny8_ram" in body
    assert "tiny8_cpu" in body


def test_testbench_contains_verification_tokens() -> None:
    body = render_testbench(_minimal_ir())
    assert "module tb_tiny8_top" in body
    # Verification contract
    assert "1'b1" in body  # halted
    assert "8'h2a" in body  # acc = 42
    assert "8'h14" in body  # ram[16] = 20
    assert "8'h2a" in body  # ram[17] = 42
    assert "200" in body  # max_cycles


def test_testbench_with_no_acc_is_optional() -> None:
    ir = _minimal_ir()
    # Replace with a no-acc expected
    ir2 = ir.model_copy(
        update={
            "verification": VerificationSpec(
                expected=ExpectedState(halted=True, acc=None, memory={}),
            )
        }
    )
    body = render_testbench(ir2)
    # acc_en is 1'b0, no acc compare.
    assert "1'b0" in body


def test_rtl_files_have_header_banner(tmp_path: Path) -> None:
    project = generate_project(_minimal_ir(), tmp_path)
    for rel in ALL_RTL_PATHS:
        body = (project.project_dir / rel).read_text(encoding="utf-8")
        assert "GENERATED BY ltspice-ai-agent" in body
        assert "DO NOT EDIT BY HAND" in body


def test_testbench_has_header_banner(tmp_path: Path) -> None:
    project = generate_project(_minimal_ir(), tmp_path)
    body = (project.project_dir / PATH_TB_TOP).read_text(encoding="utf-8")
    assert "GENERATED BY ltspice-ai-agent" in body


# ---------------------------------------------------------------------------
# Program file
# ---------------------------------------------------------------------------


def test_program_asm_is_written(tmp_path: Path) -> None:
    project = generate_project(_minimal_ir(), tmp_path)
    body = (project.project_dir / PATH_PROGRAM_ASM).read_text(encoding="utf-8")
    assert "LDI 20" in body
    assert "HALT" in body


def test_program_mem_has_correct_word_count(tmp_path: Path) -> None:
    project = generate_project(_minimal_ir(), tmp_path)
    body = (project.project_dir / PATH_PROGRAM_MEM).read_text(encoding="utf-8")
    lines = [ln for ln in body.splitlines() if ln.strip()]
    assert len(lines) == 256  # default rom size
    # First six lines encode LDI/STA/LDI/ADD/STA/HALT for the demo.
    from ltagent.digital_asm import OP_ADD, OP_HALT, OP_LDI, OP_STA, _encode

    assert lines[0] == f"{_encode(OP_LDI, 20):04x}"
    assert lines[1] == f"{_encode(OP_STA, 0x10):04x}"
    assert lines[2] == f"{_encode(OP_LDI, 22):04x}"
    assert lines[3] == f"{_encode(OP_ADD, 0x10):04x}"
    assert lines[4] == f"{_encode(OP_STA, 0x11):04x}"
    assert lines[5] == f"{_encode(OP_HALT, 0):04x}"


# ---------------------------------------------------------------------------
# Custom program
# ---------------------------------------------------------------------------


def test_generate_project_with_custom_program(tmp_path: Path) -> None:
    project = generate_project(
        _minimal_ir(),
        tmp_path,
        program=[("LDI", 1), ("HALT", 0)],
    )
    body = (project.project_dir / PATH_PROGRAM_MEM).read_text(encoding="utf-8")
    lines = [ln for ln in body.splitlines() if ln.strip()]
    from ltagent.digital_asm import OP_HALT, OP_LDI, _encode

    assert lines[0] == f"{_encode(OP_LDI, 1):04x}"
    assert lines[1] == f"{_encode(OP_HALT, 0):04x}"


def test_generate_project_with_custom_asm_source(tmp_path: Path) -> None:
    src = "LDI 5\nHALT\n"
    project = generate_project(_minimal_ir(), tmp_path, program_source=src)
    body = (project.project_dir / PATH_PROGRAM_ASM).read_text(encoding="utf-8")
    assert "LDI 5" in body


def test_generate_project_with_bad_program_source_emits_warning(tmp_path: Path) -> None:
    project = generate_project(
        _minimal_ir(),
        tmp_path,
        program_source="FOO 1\n",
    )
    assert any("asm" in w.lower() for w in project.warnings)
    # And the project is still written (with NOPs).
    body = (project.project_dir / PATH_PROGRAM_MEM).read_text(encoding="utf-8")
    assert "0000" in body


# ---------------------------------------------------------------------------
# Spice companion
# ---------------------------------------------------------------------------


def test_spice_companion_present(tmp_path: Path) -> None:
    project = generate_project(_minimal_ir(), tmp_path)
    body = (project.project_dir / PATH_SPICE_COMPANION).read_text(encoding="utf-8")
    assert "VCLK" in body
    assert "PULSE" in body
    assert ".tran" in body


def test_spice_companion_clock_period_scales_with_frequency(tmp_path: Path) -> None:
    ir = _minimal_ir()
    ir2 = ir.model_copy(update={"clock": ir.clock.model_copy(update={"frequencyHz": 100000})})
    project = generate_project(ir2, tmp_path)
    body = (project.project_dir / PATH_SPICE_COMPANION).read_text(encoding="utf-8")
    # 100kHz -> period 10us -> "10u"
    assert "10u" in body
