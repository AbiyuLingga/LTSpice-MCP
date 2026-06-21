"""Tiny8 assembler (Phase 12, v1).

Turns a Tiny8 assembly program into 16-bit instruction words, one
per line, in hex (no leading ``0x``). Labels are resolved in a
single pass; the output ``.mem`` is ready to feed the
``tiny8_rom`` Verilog module.

ISA (from docs/digital/plan-tiny8-agent.md §5):

========== ============= =======================================
Opcode     Mnemonic      Semantics
========== ============= =======================================
0x0        NOP           no state change, pc++
0x1        LDI imm       acc = imm
0x2        LDA addr      acc = ram[addr]
0x3        STA addr      ram[addr] = acc
0x4        ADD addr      acc = acc + ram[addr] (mod 256)
0x5        SUB addr      acc = acc - ram[addr] (mod 256)
0x6        AND addr      acc = acc & ram[addr]
0x7        OR addr       acc = acc | ram[addr]
0x8        XOR addr      acc = acc ^ ram[addr]
0x9        JMP addr      pc = addr
0xA        JZ addr       if zero_flag then pc = addr else pc++
0xB        JNZ addr      if not zero_flag then pc = addr else pc++
0xC        OUT port      out_valid=1, out_port=port, out_data=acc
0xD        IN port       acc = in_data when in_valid
0xE        reserved      illegal_instruction
0xF        HALT          halted = 1
========== ============= =======================================

Instruction layout: ``opcode[15:12] | mode[11:8] | operand[7:0]``.
``mode`` is reserved at 0 in v1. The assembler always emits
``mode = 0``.

Syntax (v1):

* One instruction per line.
* Mnemonic is case-insensitive (``LDI`` and ``ldi`` are the same).
* Operand is decimal, ``0x`` hex, or ``0b`` binary. ``HALT`` and
  ``NOP`` take no operand.
* Labels: ``name:`` on its own line, or as the first token of a
  line (the colon is required). Labels are 1..31 characters,
  start with a letter or underscore, and may contain letters,
  digits, and underscores.
* Comments: ``;`` or ``#`` to end of line.
* Whitespace is collapsed.

Output:

* 16-bit hex words, one per line, no leading ``0x``, no address
  prefix, no comments. Lines that are pure comments or pure
  blank lines do not emit a word. The ROM is padded to the
  configured ``romWords`` with ``0x0000`` (which decodes to
  ``NOP``).

Errors:

* ``ASM_UNKNOWN_OPCODE`` — mnemonic not in the ISA table.
* ``ASM_BAD_OPERAND`` — operand out of range, missing for a
  unary instruction, or present for ``NOP`` / ``HALT``.
* ``ASM_BAD_NUMBER`` — operand string is not a valid int literal.
* ``ASM_LABEL_DUPLICATE`` — same label declared twice.
* ``ASM_LABEL_UNKNOWN`` — operand references a label that was
  never declared.
* ``ASM_RESERVED_OPCODE`` — the source used ``0xE`` literally
  (or a mnemonic mapped to it; v1 has no mnemonic for it).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

# Opcode constants. Kept in one place so the generator and tests
# share them.
OP_NOP: int = 0x0
OP_LDI: int = 0x1
OP_LDA: int = 0x2
OP_STA: int = 0x3
OP_ADD: int = 0x4
OP_SUB: int = 0x5
OP_AND: int = 0x6
OP_OR: int = 0x7
OP_XOR: int = 0x8
OP_JMP: int = 0x9
OP_JZ: int = 0xA
OP_JNZ: int = 0xB
OP_OUT: int = 0xC
OP_IN: int = 0xD
OP_RESERVED: int = 0xE
OP_HALT: int = 0xF

# Reserved opcode is never emitted. The assembler refuses any
# line that would produce it. This is structural safety: the
# Tiny8 control unit treats 0xE as illegal_instruction.

# Operand width: 8 bits (matches Tiny8 datapath).
_OPERAND_BITS: int = 8
_OPERAND_MAX: int = (1 << _OPERAND_BITS) - 1  # 255
_ROM_MAX: int = (1 << 8) - 1  # addressWidth=8 -> 256 words

# Mnemonic -> opcode. ``HALT`` and ``NOP`` take no operand.
_MNEMONIC_OPCODE: Mapping[str, int] = {
    "NOP": OP_NOP,
    "LDI": OP_LDI,
    "LDA": OP_LDA,
    "STA": OP_STA,
    "ADD": OP_ADD,
    "SUB": OP_SUB,
    "AND": OP_AND,
    "OR": OP_OR,
    "XOR": OP_XOR,
    "JMP": OP_JMP,
    "JZ": OP_JZ,
    "JNZ": OP_JNZ,
    "OUT": OP_OUT,
    "IN": OP_IN,
    "HALT": OP_HALT,
}

# Instructions that take no operand.
_NULLARY_MNEMONICS: frozenset[str] = frozenset({"NOP", "HALT"})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AsmError:
    """A single structured assembler error."""

    code: str
    line: int
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code, "line": self.line, "detail": self.detail}


class AssemblerError(Exception):
    """Raised when the assembler rejects a program.

    Holds the list of structured errors so callers (CLI, MCP,
    generator) can render them directly into the JSON contract.
    """

    def __init__(self, errors: list[AsmError]) -> None:
        self.errors = list(errors)
        super().__init__(f"Assembler rejected program: {len(self.errors)} error(s)")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssembleResult:
    """The output of :func:`assemble_program`.

    ``words`` is the list of 16-bit instructions in program order.
    ``labels`` maps label name to the ROM address of the *next*
    instruction (i.e. the address used as the operand of ``JMP``,
    ``JZ``, ``JNZ``).
    """

    words: list[int]
    labels: dict[str, int]
    rom_size: int

    def to_mem_text(self) -> str:
        """Render as the ``.mem`` text format (one hex word per line)."""
        return "\n".join(f"{w:04x}" for w in self.words) + "\n"


def assemble_program(source: str, *, rom_size: int = 256) -> AssembleResult:
    """Assemble a Tiny8 program.

    Two passes: first pass registers labels (and records any
    label-naming / duplicate-label errors); second pass emits
    instruction words. Forward references are resolved in the
    second pass.

    Args:
        source: the .asm text. Newline-separated.
        rom_size: number of ROM words to pad to. Must be in
            [1, 256]. The padded words are ``0x0000`` (``NOP``).

    Raises:
        ValueError: if ``rom_size`` is out of range.
        AssemblerError: if the source has any errors.
    """
    if not 1 <= rom_size <= _ROM_MAX + 1:
        raise ValueError(f"rom_size must be in [1, {_ROM_MAX + 1}], got {rom_size}")

    errors: list[AsmError] = []
    labels: dict[str, int] = {}
    # Each entry: (line_no, label_or_None, instruction_line_or_None)
    # Captured for the second pass.
    parsed: list[tuple[int, str | None, str | None]] = []
    word_address = 0

    # First pass: register labels and split label/instruction.
    for line_no, raw in enumerate(source.splitlines(), start=1):
        line = _strip_comment(raw).strip()
        if not line:
            continue

        if _is_label_only(line):
            label = _extract_label(line)
            err = _register_label(labels, label, line_no, word_address)
            if err is not None:
                errors.append(err)
            continue

        if ":" in line:
            head, _, tail = line.partition(":")
            label = head.strip()
            tail = tail.strip()
            err = _register_label(labels, label, line_no, word_address)
            if err is not None:
                errors.append(err)
            if not tail:
                continue
            parsed.append((line_no, label, tail))
            word_address += 1
        else:
            parsed.append((line_no, None, line))
            word_address += 1

    # Second pass: assemble. Forward references resolve here.
    words: list[int] = []
    for line_no, _label, instr in parsed:
        if instr is None:
            continue
        word, errs = _assemble_instruction(instr, line_no, len(words), labels)
        errors.extend(errs)
        if word is not None:
            words.append(word)

    # Final check: out-of-ROM
    if len(words) > rom_size:
        errors.append(
            AsmError(
                code="ASM_PROGRAM_TOO_LARGE",
                line=0,
                detail=(f"program has {len(words)} instructions but rom_size is {rom_size}"),
            )
        )

    if errors:
        raise AssemblerError(errors)

    # Pad to rom_size with NOPs
    while len(words) < rom_size:
        words.append(0x0000)

    return AssembleResult(words=words, labels=labels, rom_size=rom_size)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_LABEL_RE: re.Pattern[str] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,30}$")


def _strip_comment(line: str) -> str:
    """Strip ``;`` and ``#`` comments to end of line. The first one
    wins; the rest of the line is kept if the marker is inside a
    string literal (v1 has no string literals, so this is moot).
    """
    for marker in (";", "#"):
        idx = line.find(marker)
        if idx >= 0:
            return line[:idx]
    return line


def _is_label_only(line: str) -> bool:
    return line.endswith(":") and not line.startswith(":")


def _extract_label(line: str) -> str:
    return line.rstrip(":").strip()


def _register_label(
    labels: dict[str, int], name: str, line_no: int, address: int
) -> AsmError | None:
    if not _LABEL_RE.match(name):
        return AsmError(
            code="ASM_BAD_LABEL",
            line=line_no,
            detail=(f"label {name!r} must match {_LABEL_RE.pattern}"),
        )
    if name in labels:
        return AsmError(
            code="ASM_LABEL_DUPLICATE",
            line=line_no,
            detail=f"label {name!r} already declared",
        )
    labels[name] = address
    return None


def _assemble_instruction(
    line: str, line_no: int, address: int, labels: Mapping[str, int]
) -> tuple[int | None, list[AsmError]]:
    """Assemble one instruction. Returns (word, errors)."""
    tokens = line.split()
    if not tokens:
        return None, [
            AsmError(
                code="ASM_EMPTY_INSTRUCTION",
                line=line_no,
                detail="empty instruction after comment strip",
            )
        ]

    mnemonic = tokens[0].upper()
    operands = tokens[1:]

    if mnemonic not in _MNEMONIC_OPCODE:
        return None, [
            AsmError(
                code="ASM_UNKNOWN_OPCODE",
                line=line_no,
                detail=(f"unknown mnemonic {tokens[0]!r}; valid: {sorted(_MNEMONIC_OPCODE)}"),
            )
        ]

    opcode = _MNEMONIC_OPCODE[mnemonic]

    if mnemonic in _NULLARY_MNEMONICS:
        if operands:
            return None, [
                AsmError(
                    code="ASM_BAD_OPERAND",
                    line=line_no,
                    detail=(f"{mnemonic} takes no operand, got {operands!r}"),
                )
            ]
        return _encode(opcode, 0), []

    # Operand required
    if not operands:
        return None, [
            AsmError(
                code="ASM_BAD_OPERAND",
                line=line_no,
                detail=f"{mnemonic} requires an operand",
            )
        ]
    if len(operands) > 1:
        return None, [
            AsmError(
                code="ASM_BAD_OPERAND",
                line=line_no,
                detail=(f"{mnemonic} takes exactly one operand, got {len(operands)}"),
            )
        ]

    raw = operands[0]
    value, err = _parse_operand(raw, line_no, labels)
    if err is not None:
        return None, [err]
    return _encode(opcode, value), []


def _parse_operand(
    raw: str, line_no: int, labels: Mapping[str, int]
) -> tuple[int, AsmError | None]:
    # If it looks like a label (alphanumeric, starts with letter/_, no 0x/0b),
    # try to resolve.
    if _LABEL_RE.match(raw) and not raw.lower().startswith(("0x", "0b")):
        if raw not in labels:
            return 0, AsmError(
                code="ASM_LABEL_UNKNOWN",
                line=line_no,
                detail=f"unknown label {raw!r}",
            )
        return labels[raw], None

    # Numeric literal
    try:
        if raw.lower().startswith("0x"):
            value = int(raw, 16)
        elif raw.lower().startswith("0b"):
            value = int(raw, 2)
        else:
            value = int(raw, 10)
    except ValueError:
        return 0, AsmError(
            code="ASM_BAD_NUMBER",
            line=line_no,
            detail=f"operand {raw!r} is not a valid int literal",
        )

    if not 0 <= value <= _OPERAND_MAX:
        return 0, AsmError(
            code="ASM_BAD_OPERAND",
            line=line_no,
            detail=(f"operand {value} out of range for 8-bit field (0..{_OPERAND_MAX})"),
        )
    return value, None


def _encode(opcode: int, operand: int) -> int:
    # Instruction: opcode[15:12] | mode[11:8] | operand[7:0]
    if opcode == OP_RESERVED:
        # Defensive: the assembler never emits 0xE. If a future
        # version ever exposes it via mnemonic, refuse loudly.
        raise ValueError("refusing to encode reserved opcode 0xE")
    return ((opcode & 0xF) << 12) | (operand & 0xFF)


# ---------------------------------------------------------------------------
# Convenience for the generator
# ---------------------------------------------------------------------------


def program_to_mem_lines(
    program: Iterable[tuple[str, int]],
    *,
    rom_size: int = 256,
) -> str:
    """Render a sequence of (mnemonic, operand) tuples as a .mem
    string. Used by the generator's default demo program.

    Raises:
        ValueError: on any encoding failure.
    """
    words: list[int] = []
    for mnemonic, operand in program:
        m = mnemonic.upper()
        if m not in _MNEMONIC_OPCODE:
            raise ValueError(f"unknown mnemonic {mnemonic!r}")
        words.append(_encode(_MNEMONIC_OPCODE[m], int(operand)))
    while len(words) < rom_size:
        words.append(0x0000)
    return "\n".join(f"{w:04x}" for w in words) + "\n"


__all__ = [
    "OP_ADD",
    "OP_AND",
    "OP_HALT",
    "OP_IN",
    "OP_JMP",
    "OP_JNZ",
    "OP_JZ",
    "OP_LDA",
    "OP_LDI",
    "OP_NOP",
    "OP_OR",
    "OP_OUT",
    "OP_RESERVED",
    "OP_STA",
    "OP_SUB",
    "OP_XOR",
    "AsmError",
    "AssembleResult",
    "AssemblerError",
    "assemble_program",
    "program_to_mem_lines",
]
