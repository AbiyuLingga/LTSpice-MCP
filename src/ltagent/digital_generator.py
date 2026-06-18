"""Phase 12: Tiny8 project generator.

Turns a validated ``DesignIR`` + an assembled ``demo.mem`` into
the full project artefact set under a target directory:

  <project>/
    design.ir.json
    manifest.json
    rtl/
      tiny8_alu.v
      tiny8_control.v
      tiny8_rom.v
      tiny8_ram.v
      tiny8_cpu.v
      tiny8_top.v
    tb/
      tb_tiny8_top.v
    programs/
      demo.asm
      demo.mem
    spice/
      reset_clock_companion.cir

The generator is deterministic: given the same ``DesignIR`` and
the same program source, it produces byte-identical files. This
is what the snapshot test in ``test_digital_generator`` checks.

The generator **never** reads from a prompt or an LLM. It
takes a ``DesignIR`` (which the planner produced or the user
hand-wrote) and a list of (mnemonic, operand) tuples for the
program, and writes the files. The LLM is not in this code
path.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .digital_asm import (
    OP_NOP,
    AssembleResult,
    assemble_program,
    program_to_mem_lines,
)
from .digital_ir import DesignIR
from .digital_templates import (
    GENERATED_HEADER,
    TINY8_ALU_V,
    TINY8_CONTROL_V,
    TINY8_CPU_V,
    TINY8_RAM_V,
    TINY8_ROM_V,
    TINY8_SOC_TOP_V,
    TINY8_TB_V,
    TINY8_TOP_V,
)

# ---------------------------------------------------------------------------
# Manifest and result contracts (small dataclasses; the on-disk
# JSON shape is the plan doc's manifest.json / result.json).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GeneratedFile:
    """One file the generator wrote."""

    relative_path: str
    byte_size: int
    sha256_short: str  # first 12 hex chars


@dataclass(frozen=True)
class GeneratedProject:
    """The result of :func:`generate_project`."""

    project_dir: Path
    files: list[GeneratedFile] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# File paths (relative to project dir)
# ---------------------------------------------------------------------------

PATH_DESIGN_IR = "design.ir.json"
PATH_MANIFEST = "manifest.json"
PATH_RESULT = "result.json"

PATH_RTL_ALU = "rtl/tiny8_alu.v"
PATH_RTL_CONTROL = "rtl/tiny8_control.v"
PATH_RTL_ROM = "rtl/tiny8_rom.v"
PATH_RTL_RAM = "rtl/tiny8_ram.v"
PATH_RTL_CPU = "rtl/tiny8_cpu.v"
PATH_RTL_TOP = "rtl/tiny8_top.v"

PATH_TB_TOP = "tb/tb_tiny8_top.v"

PATH_PROGRAM_ASM = "programs/demo.asm"
PATH_PROGRAM_MEM = "programs/demo.mem"

PATH_SPICE_COMPANION = "spice/reset_clock_companion.cir"

ALL_RTL_PATHS: tuple[str, ...] = (
    PATH_RTL_ALU,
    PATH_RTL_CONTROL,
    PATH_RTL_ROM,
    PATH_RTL_RAM,
    PATH_RTL_CPU,
    PATH_RTL_TOP,
)

ALL_TESTBENCH_PATHS: tuple[str, ...] = (PATH_TB_TOP,)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def render_alu(ir: DesignIR) -> str:
    return TINY8_ALU_V.replace("__HEADER__", GENERATED_HEADER)


def render_control(ir: DesignIR) -> str:
    return TINY8_CONTROL_V.replace("__HEADER__", GENERATED_HEADER)


def render_rom(ir: DesignIR) -> str:
    return TINY8_ROM_V.replace("__HEADER__", GENERATED_HEADER)


def render_ram(ir: DesignIR) -> str:
    return TINY8_RAM_V.replace("__HEADER__", GENERATED_HEADER)


def render_cpu(ir: DesignIR) -> str:
    return TINY8_CPU_V.replace("__HEADER__", GENERATED_HEADER)


def render_top(ir: DesignIR) -> str:
    return TINY8_TOP_V.replace("__HEADER__", GENERATED_HEADER)


def render_soc_top(ir: DesignIR) -> str:
    return TINY8_SOC_TOP_V.replace("__HEADER__", GENERATED_HEADER)


def render_testbench(ir: DesignIR) -> str:
    """Render the testbench with the verification contract filled in.

    The tokens in the template are:
      __HEADER__        do-not-edit banner
      __EXP_HALTED__    "1'b1" if verification.expected.halted is true
      __EXP_ACC_EN__    "1'b0" or "1'b1" — whether the testbench checks acc
      __EXP_ACC__       the expected acc as 8'hNN (only valid if __EXP_ACC_EN__)
      __MAX_CYCLES__    program.expectedHaltCyclesMax (decimal)
      __EXP_MEM_CHECKS__  one Verilog ``$display`` line per expected memory entry
    """
    expected = ir.verification.expected
    halted_bit = "1'b1" if expected.halted else "1'b0"
    if expected.acc is None:
        acc_en = "1'b0"
        acc_val = "8'h00"
    else:
        acc_en = "1'b1"
        acc_val = f"8'h{expected.acc:02x}"

    mem_lines: list[str] = []
    for key, value in sorted(
        expected.memory_as_int_dict().items(), key=lambda kv: kv[0]
    ):
        mem_lines.append(
            f"                if (ram_mem[{key}] !== 8'h{value:02x}) begin\n"
            f"                    $display(\"TB_FAIL ram[{key}] expected 8'h{value:02x} got %h\", ram_mem[{key}]);\n"
            f"                    failed = 1'b1;\n"
            f"                end"
        )
    mem_block = "\n".join(mem_lines)
    if not mem_block:
        mem_block = "                // no memory checks requested"

    body = TINY8_TB_V
    body = body.replace("__HEADER__", GENERATED_HEADER)
    body = body.replace("__EXP_HALTED__", halted_bit)
    body = body.replace("__EXP_ACC_EN__", acc_en)
    body = body.replace("__EXP_ACC__", acc_val)
    body = body.replace("__MAX_CYCLES__", str(ir.program.expectedHaltCyclesMax))
    body = body.replace("__EXP_MEM_CHECKS__", mem_block)
    return body


def render_manifest(ir: DesignIR) -> str:
    """Render the on-disk ``manifest.json`` describing the project.

    The shape is documented in
    ``docs/digital/plan-tiny8-agent.md`` §10.1.
    """
    import json

    payload = {
        "schemaVersion": "0.1",
        "projectKind": "digital",
        "designKind": ir.kind,
        "topModule": "tiny8_top",
        "sourceIr": PATH_DESIGN_IR,
        "rtlFiles": list(ALL_RTL_PATHS),
        "testbenches": list(ALL_TESTBENCH_PATHS),
        "programs": [PATH_PROGRAM_ASM, PATH_PROGRAM_MEM],
        "reports": ["reports/sim.json", "reports/synth.json"],
        "createdBy": ir.metadata.createdBy,
        "designName": ir.name,
        "isa": ir.cpu.isa,
        "dataWidth": ir.cpu.dataWidth,
        "addressWidth": ir.cpu.addressWidth,
        "instructionWidth": ir.cpu.instructionWidth,
    }
    return json.dumps(payload, indent=2) + "\n"


def render_default_program() -> str:
    """The default demo program in .asm syntax.

    The exact source that yields the "add 20 + 22 and halt" demo
    verified by the canonical ``DesignIR``. Used when the user
    accepts the default or does not supply a custom program.
    """
    return (
        "; Tiny8 default v1 demo program\n"
        "; Computes 20 + 22 and stores the result at RAM[0x11].\n"
        "LDI 20\n"
        "STA 0x10\n"
        "LDI 22\n"
        "ADD 0x10\n"
        "STA 0x11\n"
        "HALT\n"
    )


def render_spice_companion(ir: DesignIR) -> str:
    """LTspice analog companion: clock + reset RC.

    This is *not* the digital core. It is the simple analog glue
    that turns a board-level clock and reset pushbutton into
    clean ``clk`` and ``rst`` for the Tiny8. It also serves as
    a sanity check that the LTspice toolchain can still render
    and simulate a project adjacent to the digital artefacts.
    """
    clk_hz = ir.clock.frequencyHz
    # Period = 1 / frequency. Format with the most readable suffix.
    period_s = 1.0 / float(clk_hz)
    if period_s >= 1.0:
        clk_period = f"{period_s:.4g}"
    elif period_s >= 1e-3:
        clk_period = f"{period_s * 1e3:.4g}m"
    elif period_s >= 1e-6:
        clk_period = f"{period_s * 1e6:.4g}u"
    elif period_s >= 1e-9:
        clk_period = f"{period_s * 1e9:.4g}n"
    else:
        clk_period = f"{period_s * 1e12:.4g}p"

    return f"""\
* Tiny8 analog companion (Phase 12, v1)
* Clock source + RC reset. Drives clk/rst into tiny8_top.
* Generated by ltspice-ai-agent; do not edit by hand.

* --- clock source ---
VCLK  clk 0 PULSE(0 3.3 1n 1n 1n {clk_period} 1)

* --- reset RC: button pull-up + debounce cap ---
VCC  vcc 0 3.3
R_RST  vcc  rst  10k
C_RST  rst  0    100n
S_RST  rst  0    0  rst_btn 0
VBTN  rst_btn 0 PWL(0 0 1m 0 1.001m 3.3)

* --- load on clk (so the simulation does something measurable) ---
R_CLK clk 0 10k

.tran 10n 200u
.end
"""


# ---------------------------------------------------------------------------
# Project orchestrator
# ---------------------------------------------------------------------------


def generate_project(
    ir: DesignIR,
    project_dir: str | Path,
    *,
    program_source: str | None = None,
    program: Iterable[tuple[str, int]] | None = None,
) -> GeneratedProject:
    """Render all files and write them under ``project_dir``.

    Either ``program_source`` (.asm text) or ``program`` (a
    sequence of (mnemonic, operand) tuples) drives the .mem. If
    both are provided, ``program_source`` wins. If neither is
    given, the default v1 demo program is used.
    """
    project_dir = Path(project_dir)
    warnings: list[str] = []

    if program_source is None and program is None:
        program_source = render_default_program()

    if program_source is not None:
        try:
            asm_result: AssembleResult = assemble_program(
                program_source, rom_size=ir.memory.romWords
            )
        except Exception as exc:
            # Programmer error — surface as a warning but still try
            # to write the rest of the project.
            warnings.append(f"asm error: {exc}")
            asm_result = AssembleResult(
                words=[OP_NOP] * ir.memory.romWords,
                labels={},
                rom_size=ir.memory.romWords,
            )
    else:
        # Caller passed a pre-built (mnemonic, operand) sequence.
        program_seq = program if program is not None else []
        try:
            mem_text = program_to_mem_lines(
                list(program_seq), rom_size=ir.memory.romWords
            )
        except Exception as exc:
            warnings.append(f"program render error: {exc}")
            mem_text = "0000\n" * ir.memory.romWords
        asm_result = AssembleResult(
            words=_words_from_mem_text(mem_text),
            labels={},
            rom_size=ir.memory.romWords,
        )

    files: list[tuple[str, str]] = []

    # IR
    files.append((PATH_DESIGN_IR, ir.model_dump_json(indent=2) + "\n"))

    # RTL
    files.append((PATH_RTL_ALU, render_alu(ir)))
    files.append((PATH_RTL_CONTROL, render_control(ir)))
    files.append((PATH_RTL_ROM, render_rom(ir)))
    files.append((PATH_RTL_RAM, render_ram(ir)))
    files.append((PATH_RTL_CPU, render_cpu(ir)))
    files.append((PATH_RTL_TOP, render_top(ir)))

    # Testbench
    files.append((PATH_TB_TOP, render_testbench(ir)))

    # Program
    src_text = (
        program_source
        if program_source is not None
        else _render_program_from_tuples(list(program or []))
    )
    mem_text = "\n".join(f"{w:04x}" for w in asm_result.words) + "\n"
    files.append((PATH_PROGRAM_ASM, src_text))
    files.append((PATH_PROGRAM_MEM, mem_text))

    # Manifest
    files.append((PATH_MANIFEST, render_manifest(ir)))

    # LTspice companion
    files.append((PATH_SPICE_COMPANION, render_spice_companion(ir)))

    # Write
    written: list[GeneratedFile] = []
    for relpath, body in files:
        full = project_dir / relpath
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(body, encoding="utf-8")
        written.append(
            GeneratedFile(
                relative_path=relpath,
                byte_size=len(body.encode("utf-8")),
                sha256_short=_short_sha256(body.encode("utf-8")),
            )
        )

    return GeneratedProject(
        project_dir=project_dir, files=written, warnings=warnings
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _short_sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()[:12]


def _words_from_mem_text(mem_text: str) -> list[int]:
    out: list[int] = []
    for line in mem_text.splitlines():
        s = line.strip()
        if not s:
            continue
        out.append(int(s, 16))
    return out


def _render_program_from_tuples(program: list[tuple[str, int]]) -> str:
    lines = [
        "; Tiny8 program (default)",
        "; Generated by ltspice-ai-agent; edit and re-assemble if needed.",
    ]
    for mnemonic, operand in program:
        if mnemonic.upper() in {"NOP", "HALT"}:
            lines.append(mnemonic.upper())
        else:
            lines.append(f"{mnemonic.upper()} {operand}")
    return "\n".join(lines) + "\n"


__all__ = [
    "ALL_RTL_PATHS",
    "ALL_TESTBENCH_PATHS",
    "PATH_DESIGN_IR",
    "PATH_MANIFEST",
    "PATH_PROGRAM_ASM",
    "PATH_PROGRAM_MEM",
    "PATH_RESULT",
    "PATH_RTL_ALU",
    "PATH_RTL_CONTROL",
    "PATH_RTL_CPU",
    "PATH_RTL_RAM",
    "PATH_RTL_ROM",
    "PATH_RTL_TOP",
    "PATH_SPICE_COMPANION",
    "PATH_TB_TOP",
    "GeneratedFile",
    "GeneratedProject",
    "generate_project",
    "render_alu",
    "render_control",
    "render_cpu",
    "render_default_program",
    "render_manifest",
    "render_ram",
    "render_rom",
    "render_soc_top",
    "render_spice_companion",
    "render_testbench",
    "render_top",
]
