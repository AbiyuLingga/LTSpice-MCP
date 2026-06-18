"""Tests for ``ltagent.digital_asm`` (Phase 12, Tiny8 assembler).

Covers the ISA encoding, label resolution, and error codes.
"""

from __future__ import annotations

import pytest

from ltagent.digital_asm import (
    OP_ADD,
    OP_HALT,
    OP_JZ,
    OP_LDI,
    OP_NOP,
    OP_RESERVED,
    OP_STA,
    AsmError,
    AssemblerError,
    assemble_program,
    program_to_mem_lines,
)


def _enc(op: int, operand: int) -> int:
    return ((op & 0xF) << 12) | (operand & 0xFF)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_assemble_demo_program() -> None:
    src = (
        "LDI 20\n"
        "STA 0x10\n"
        "LDI 22\n"
        "ADD 0x10\n"
        "STA 0x11\n"
        "HALT\n"
    )
    r = assemble_program(src, rom_size=8)
    assert r.words == [
        _enc(OP_LDI, 20),
        _enc(OP_STA, 0x10),
        _enc(OP_LDI, 22),
        _enc(OP_ADD, 0x10),
        _enc(OP_STA, 0x11),
        _enc(OP_HALT, 0),
        0,
        0,
    ]


def test_assemble_case_insensitive_mnemonic() -> None:
    src = "ldi 5\nHALT\n"
    r = assemble_program(src, rom_size=4)
    assert r.words[0] == _enc(OP_LDI, 5)


def test_assemble_strips_comments() -> None:
    src = "; leading comment\nLDI 1 ; inline\n# also inline\nSTA 0x10\nHALT\n"
    r = assemble_program(src, rom_size=4)
    assert r.words == [
        _enc(OP_LDI, 1),
        _enc(OP_STA, 0x10),
        _enc(OP_HALT, 0),
        0,
    ]


def test_assemble_binary_literal() -> None:
    r = assemble_program("LDI 0b1010\nHALT\n", rom_size=2)
    assert r.words[0] == _enc(OP_LDI, 0b1010)


def test_assemble_resolves_label() -> None:
    src = "JMP start\nNOP\nstart: HALT\n"
    r = assemble_program(src, rom_size=4)
    # start is at address 2
    assert r.words[0] == _enc(0x9, 2)
    assert r.labels == {"start": 2}


def test_assemble_label_before_instruction() -> None:
    src = "loop: JMP loop\nHALT\n"
    r = assemble_program(src, rom_size=2)
    assert r.words[0] == _enc(0x9, 0)
    assert r.labels == {"loop": 0}


def test_assemble_pads_to_rom_size() -> None:
    r = assemble_program("NOP\n", rom_size=8)
    assert len(r.words) == 8
    assert all(w == 0 for w in r.words[1:])


def test_assemble_default_rom_size_is_256() -> None:
    r = assemble_program("HALT\n")
    assert r.rom_size == 256
    assert len(r.words) == 256


def test_assemble_to_mem_text_format() -> None:
    src = "LDI 1\nHALT\n"
    r = assemble_program(src, rom_size=4)
    text = r.to_mem_text()
    lines = text.strip().splitlines()
    assert lines[0] == f"{_enc(OP_LDI, 1):04x}"
    assert lines[1] == f"{_enc(OP_HALT, 0):04x}"
    assert lines[2] == "0000"
    assert lines[3] == "0000"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_unknown_mnemonic_is_asm_unknown_opcode() -> None:
    with pytest.raises(AssemblerError) as exc:
        assemble_program("FOO 1\n", rom_size=2)
    assert exc.value.errors[0].code == "ASM_UNKNOWN_OPCODE"
    assert exc.value.errors[0].line == 1


def test_duplicate_label_is_asm_label_duplicate() -> None:
    src = "loop: NOP\nloop: HALT\n"
    with pytest.raises(AssemblerError) as exc:
        assemble_program(src, rom_size=4)
    assert exc.value.errors[0].code == "ASM_LABEL_DUPLICATE"
    assert exc.value.errors[0].line == 2


def test_unknown_label_is_asm_label_unknown() -> None:
    with pytest.raises(AssemblerError) as exc:
        assemble_program("JMP never\n", rom_size=2)
    assert exc.value.errors[0].code == "ASM_LABEL_UNKNOWN"
    assert exc.value.errors[0].line == 1


def test_operand_out_of_range_is_asm_bad_operand() -> None:
    with pytest.raises(AssemblerError) as exc:
        assemble_program("LDI 256\n", rom_size=2)
    assert exc.value.errors[0].code == "ASM_BAD_OPERAND"
    assert "out of range" in exc.value.errors[0].detail


def test_operand_missing_for_unary_is_asm_bad_operand() -> None:
    with pytest.raises(AssemblerError) as exc:
        assemble_program("LDI\n", rom_size=2)
    assert exc.value.errors[0].code == "ASM_BAD_OPERAND"
    assert "requires" in exc.value.errors[0].detail


def test_operand_for_nop_is_asm_bad_operand() -> None:
    with pytest.raises(AssemblerError) as exc:
        assemble_program("NOP 1\n", rom_size=2)
    assert exc.value.errors[0].code == "ASM_BAD_OPERAND"
    assert "takes no operand" in exc.value.errors[0].detail


def test_halt_with_operand_is_asm_bad_operand() -> None:
    with pytest.raises(AssemblerError) as exc:
        assemble_program("HALT 5\n", rom_size=2)
    assert exc.value.errors[0].code == "ASM_BAD_OPERAND"


def test_bad_number_is_asm_bad_number() -> None:
    with pytest.raises(AssemblerError) as exc:
        assemble_program("LDI 0xZZ\n", rom_size=2)
    assert exc.value.errors[0].code == "ASM_BAD_NUMBER"


def test_program_too_large_is_asm_program_too_large() -> None:
    src = "NOP\nNOP\nNOP\nNOP\nNOP\n"  # 5 instructions
    with pytest.raises(AssemblerError) as exc:
        assemble_program(src, rom_size=4)
    assert exc.value.errors[0].code == "ASM_PROGRAM_TOO_LARGE"


def test_bad_label_name_is_asm_bad_label() -> None:
    with pytest.raises(AssemblerError) as exc:
        assemble_program("1bad: HALT\n", rom_size=2)
    assert exc.value.errors[0].code == "ASM_BAD_LABEL"


def test_reserved_opcode_never_encoded() -> None:
    # Defensive: the assembler has no mnemonic for 0xE. We construct
    # a word with the reserved opcode by hand and confirm the
    # helper refuses.
    from ltagent.digital_asm import _encode

    with pytest.raises(ValueError):
        _encode(OP_RESERVED, 0)


# ---------------------------------------------------------------------------
# program_to_mem_lines
# ---------------------------------------------------------------------------


def test_program_to_mem_lines_known_program() -> None:
    text = program_to_mem_lines(
        [("LDI", 20), ("STA", 0x10), ("HALT", 0)], rom_size=4
    )
    lines = text.strip().splitlines()
    assert lines[0] == f"{_enc(OP_LDI, 20):04x}"
    assert lines[1] == f"{_enc(OP_STA, 0x10):04x}"
    assert lines[2] == f"{_enc(OP_HALT, 0):04x}"
    assert lines[3] == "0000"


def test_program_to_mem_lines_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        program_to_mem_lines([("FOO", 0)], rom_size=2)


def test_asm_error_to_dict_shape() -> None:
    e = AsmError(code="X", line=3, detail="bad")
    d = e.to_dict()
    assert set(d.keys()) == {"code", "line", "detail"}


# ---------------------------------------------------------------------------
# JZ / JNZ / encoding spot checks
# ---------------------------------------------------------------------------


def test_jz_encoding() -> None:
    r = assemble_program("JZ 0x20\n", rom_size=2)
    assert r.words[0] == _enc(OP_JZ, 0x20)


def test_nop_encoding() -> None:
    r = assemble_program("NOP\n", rom_size=2)
    assert r.words[0] == _enc(OP_NOP, 0)
