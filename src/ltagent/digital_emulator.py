"""Deterministic Tiny8 ISA emulator used as the digital correctness oracle.

The emulator follows the public Tiny8 ISA rather than a simulator-specific
fetch pipeline. It is intentionally pure Python so assembler, firmware, LED
peripherals, and later RTL comparison tests can run on hosts without Icarus or
Verilator.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from .digital_asm import (
    OP_ADD,
    OP_AND,
    OP_HALT,
    OP_IN,
    OP_JMP,
    OP_JNZ,
    OP_JZ,
    OP_LDA,
    OP_LDI,
    OP_NOP,
    OP_OR,
    OP_OUT,
    OP_RESERVED,
    OP_STA,
    OP_SUB,
    OP_XOR,
)

ADDRESS_SPACE: Final[int] = 256
WORD_MAX: Final[int] = 0xFFFF
BYTE_MAX: Final[int] = 0xFF


@dataclass(frozen=True)
class Tiny8State:
    """Architectural Tiny8 state after one or more instruction steps."""

    pc: int
    acc: int
    zero_flag: bool
    halted: bool
    cycles: int
    ram: tuple[int, ...]


@dataclass(frozen=True)
class OutputEvent:
    """One deterministic OUT-port event emitted by an instruction."""

    cycle: int
    port: int
    data: int


@dataclass(frozen=True)
class Tiny8Fault:
    """A program-visible execution fault, never an unstructured exception."""

    code: str
    pc: int
    word: int


@dataclass(frozen=True)
class RunResult:
    """Outcome of executing a bounded Tiny8 program."""

    state: Tiny8State
    output_events: tuple[OutputEvent, ...]
    fault: Tiny8Fault | None
    timed_out: bool

    @property
    def halted(self) -> bool:
        return self.state.halted


class Tiny8Emulator:
    """Execute validated 16-bit Tiny8 words with bounded memory and IO."""

    def __init__(
        self,
        rom: Sequence[int],
        *,
        inputs: Mapping[int, int] | None = None,
        ram: Mapping[int, int] | None = None,
    ) -> None:
        if not rom or len(rom) > ADDRESS_SPACE:
            raise ValueError(f"rom must contain between 1 and {ADDRESS_SPACE} words")
        self._rom = tuple(_validate_word(word, name="rom word") for word in rom)
        self._ram = [0] * ADDRESS_SPACE
        for address, value in (ram or {}).items():
            self._ram[_validate_byte(address, name="ram address")] = _validate_byte(
                value, name="ram value"
            )
        self._inputs = {
            _validate_byte(port, name="input port"): _validate_byte(value, name="input value")
            for port, value in (inputs or {}).items()
        }
        self._pc = 0
        self._acc = 0
        self._zero_flag = True
        self._halted = False
        self._cycles = 0
        self._fault: Tiny8Fault | None = None
        self._output_events: list[OutputEvent] = []

    @property
    def state(self) -> Tiny8State:
        return Tiny8State(
            pc=self._pc,
            acc=self._acc,
            zero_flag=self._zero_flag,
            halted=self._halted,
            cycles=self._cycles,
            ram=tuple(self._ram),
        )

    def step(self) -> Tiny8State:
        """Execute one architectural instruction unless halted or faulted."""
        if self._halted or self._fault is not None:
            return self.state
        word = self._rom[self._pc] if self._pc < len(self._rom) else 0
        opcode = (word >> 12) & 0xF
        mode = (word >> 8) & 0xF
        operand = word & BYTE_MAX
        self._cycles += 1

        if mode != 0:
            self._fault = Tiny8Fault("TINY8_RESERVED_MODE", self._pc, word)
            return self.state
        if opcode == OP_RESERVED:
            self._fault = Tiny8Fault("TINY8_RESERVED_OPCODE", self._pc, word)
            return self.state

        next_pc = (self._pc + 1) & BYTE_MAX
        if opcode == OP_NOP:
            pass
        elif opcode == OP_LDI:
            self._write_acc(operand)
        elif opcode == OP_LDA:
            self._write_acc(self._ram[operand])
        elif opcode == OP_STA:
            self._ram[operand] = self._acc
        elif opcode == OP_ADD:
            self._write_acc(self._acc + self._ram[operand])
        elif opcode == OP_SUB:
            self._write_acc(self._acc - self._ram[operand])
        elif opcode == OP_AND:
            self._write_acc(self._acc & self._ram[operand])
        elif opcode == OP_OR:
            self._write_acc(self._acc | self._ram[operand])
        elif opcode == OP_XOR:
            self._write_acc(self._acc ^ self._ram[operand])
        elif opcode == OP_JMP:
            next_pc = operand
        elif opcode == OP_JZ:
            if self._zero_flag:
                next_pc = operand
        elif opcode == OP_JNZ:
            if not self._zero_flag:
                next_pc = operand
        elif opcode == OP_OUT:
            self._output_events.append(
                OutputEvent(cycle=self._cycles, port=operand, data=self._acc)
            )
        elif opcode == OP_IN:
            self._write_acc(self._inputs.get(operand, 0))
        elif opcode == OP_HALT:
            self._halted = True
            next_pc = self._pc
        else:
            self._fault = Tiny8Fault("TINY8_UNKNOWN_OPCODE", self._pc, word)
            return self.state
        self._pc = next_pc
        return self.state

    def run(self, *, max_cycles: int) -> RunResult:
        """Execute until HALT, a structured fault, or the requested limit."""
        if max_cycles < 1:
            raise ValueError("max_cycles must be at least 1")
        while self._cycles < max_cycles and not self._halted and self._fault is None:
            self.step()
        return RunResult(
            state=self.state,
            output_events=tuple(self._output_events),
            fault=self._fault,
            timed_out=(
                self._cycles >= max_cycles and not self._halted and self._fault is None
            ),
        )

    def _write_acc(self, value: int) -> None:
        self._acc = value & BYTE_MAX
        self._zero_flag = self._acc == 0


def run_program(
    rom: Sequence[int],
    *,
    max_cycles: int,
    inputs: Mapping[int, int] | None = None,
    ram: Mapping[int, int] | None = None,
) -> RunResult:
    """Convenience API for a full bounded emulator run."""
    return Tiny8Emulator(rom, inputs=inputs, ram=ram).run(max_cycles=max_cycles)


def _validate_word(value: int, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= WORD_MAX:
        raise ValueError(f"{name} must be an unsigned 16-bit integer")
    return value


def _validate_byte(value: int, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= BYTE_MAX:
        raise ValueError(f"{name} must be an unsigned 8-bit integer")
    return value


__all__ = [
    "ADDRESS_SPACE",
    "BYTE_MAX",
    "OutputEvent",
    "RunResult",
    "Tiny8Emulator",
    "Tiny8Fault",
    "Tiny8State",
    "run_program",
]
