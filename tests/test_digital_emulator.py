"""Tiny8 ISA oracle tests independent from external HDL toolchains."""

from __future__ import annotations

from ltagent.digital_asm import assemble_program
from ltagent.digital_emulator import Tiny8Emulator, run_program


def _words(source: str) -> list[int]:
    return assemble_program(source, rom_size=32).words


def test_emulator_executes_accumulator_memory_and_branch_instructions() -> None:
    result = run_program(
        _words(
            """
            LDI 3
            STA 10
            LDI 3
            SUB 10
            JZ done
            LDI 99
        done: HALT
            """
        ),
        max_cycles=16,
    )

    assert result.halted is True
    assert result.fault is None
    assert result.state.acc == 0
    assert result.state.ram[10] == 3
    assert result.state.pc == 6


def test_emulator_records_out_events_and_reads_configured_inputs() -> None:
    emulator = Tiny8Emulator(
        _words(
            """
            LDI 42
            OUT 9
            IN 3
            HALT
            """
        ),
        inputs={3: 7},
    )

    result = emulator.run(max_cycles=8)

    assert result.output_events[0].port == 9
    assert result.output_events[0].data == 42
    assert result.state.acc == 7
    assert result.state.zero_flag is False


def test_emulator_stops_on_reserved_opcode_without_mutating_state() -> None:
    result = run_program([0xE000], max_cycles=4)

    assert result.halted is False
    assert result.fault is not None
    assert result.fault.code == "TINY8_RESERVED_OPCODE"
    assert result.state.pc == 0
    assert result.state.cycles == 1


def test_emulator_reports_a_deterministic_cycle_limit() -> None:
    result = run_program(_words("JMP 0"), max_cycles=5)

    assert result.halted is False
    assert result.timed_out is True
    assert result.state.cycles == 5
