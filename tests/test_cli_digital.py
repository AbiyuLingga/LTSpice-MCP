"""CLI tests for ``ltagent digital plan`` (Phase 12, Tiny8 CPU).

Covers:
- Happy path: prompt -> DesignIR + JSON contract shape.
- Refusal: empty / ambiguous / unsafe.
- Clarification: recognised but no program.
- Roadmap: RISC-V / mini PC.
- Stub subcommands (create / assemble / doctor / simulate / synth-check / inspect)
  return the structured ``DIGITAL_NOT_IMPLEMENTED`` warning.
- Parser surface: ``--help`` lists every subcommand.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ltagent.cli import main as cli_main


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> dict:
    """Invoke ``ltagent`` main with the given argv and return parsed JSON."""
    rc = cli_main(argv)
    captured = capsys.readouterr()
    # Strip the leading human text if any; CLI writes JSON only when
    # --json is present.
    payload = json.loads(captured.out)
    assert isinstance(payload, dict)
    return payload, rc


# ---------------------------------------------------------------------------
# digital plan
# ---------------------------------------------------------------------------


def test_digital_plan_happy_path(capsys: pytest.CaptureFixture[str]) -> None:
    payload, rc = _run(
        ["digital", "plan", "buat mini processor 8-bit sederhana add 20 22", "--json"],
        capsys,
    )
    assert rc == 0
    assert payload["success"] is True
    assert payload["command"] == "digital.plan"
    assert payload["data"]["kind"] == "tiny8_cpu"
    assert payload["data"]["design"]["verification"]["expected"]["acc"] == 42


def test_digital_plan_writes_out_file(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The CLI's --out guard requires the target to be under the
    # current working directory. cd into tmp_path for the test.
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "tiny8.design.json"
    payload, rc = _run(
        [
            "digital",
            "plan",
            "create tiny 8-bit CPU add 20 22 halt",
            "--out",
            str(out),
            "--json",
        ],
        capsys,
    )
    assert rc == 0
    assert payload["success"] is True
    assert payload["data"]["writtenTo"] == str(out)
    # File exists and parses
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["kind"] == "tiny8_cpu"


def test_digital_plan_refusal_unsafe(capsys: pytest.CaptureFixture[str]) -> None:
    payload, rc = _run(
        ["digital", "plan", "rm -rf the CPU", "--json"],
        capsys,
    )
    assert rc == 1
    assert payload["success"] is False
    assert payload["errors"][0]["code"] == "PROMPT_INJECTION"
    assert payload["data"]["supportedKinds"] == ["tiny8_cpu", "tiny8_soc"]


def test_digital_plan_clarification(capsys: pytest.CaptureFixture[str]) -> None:
    payload, rc = _run(
        ["digital", "plan", "buat mini processor 8-bit sederhana", "--json"],
        capsys,
    )
    assert rc == 1
    assert payload["success"] is False
    assert payload["data"]["needsClarification"] is True
    # The default option is offered in the options list.
    assert any("default" in opt.lower() for opt in payload["data"]["options"])
    assert payload["data"]["supportedKinds"] == ["tiny8_cpu"]


def test_digital_plan_roadmap(capsys: pytest.CaptureFixture[str]) -> None:
    payload, rc = _run(
        ["digital", "plan", "buat RISC-V processor", "--json"],
        capsys,
    )
    assert rc == 0  # roadmap is informational, not an error
    assert payload["success"] is True
    assert payload["data"]["roadmap"] is True
    assert payload["data"]["category"] in {"riscv_request", "mini_pc_full"}


def test_digital_plan_roadmap_mini_pc(capsys: pytest.CaptureFixture[str]) -> None:
    payload, rc = _run(
        ["digital", "plan", "buat mini PC lengkap dengan USB HDMI", "--json"],
        capsys,
    )
    assert rc == 0
    assert payload["data"]["roadmap"] is True


def test_digital_plan_empty_prompt(capsys: pytest.CaptureFixture[str]) -> None:
    payload, rc = _run(["digital", "plan", "", "--json"], capsys)
    assert rc == 1
    assert payload["errors"][0]["code"] == "UNSUPPORTED_PROMPT"


def test_digital_plan_rejects_path_outside_cwd(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    out = tmp_path / "elsewhere.design.json"  # outside CWD
    payload, rc = _run(
        [
            "digital",
            "plan",
            "create tiny 8-bit CPU add 20 22 halt",
            "--out",
            str(out),
            "--json",
        ],
        capsys,
    )
    assert rc == 1
    assert payload["errors"][0]["code"] == "PATH_OUTSIDE_CWD"


# ---------------------------------------------------------------------------
# Stub subcommands
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sub",
    [
        "plan",
        "create",
        "assemble",
        "doctor",
        "simulate",
        "synth-check",
        "inspect",
    ],
)
def test_digital_help_lists_all_subcommands(sub: str) -> None:
    """Smoke check: every Phase 12 subcommand is wired into the parser.

    The actual behaviour is covered by the per-subcommand tests below
    (and the new Phase D handlers). This just guards against the
    subcommand accidentally falling out of the parser.
    """
    # Use the parser directly to avoid driving the full handler.

    import contextlib

    # If the subcommand is missing, argparse exits 2 with usage.
    # We don't want that to be a hard fail; instead we check the
    # help text via the parser.
    # Simulate by calling the main with --help on the subcommand
    # group and check the help text mentions ``sub``.
    import io

    from ltagent.cli import main as _real_main

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            _real_main(["digital", "--help"])
    except SystemExit:
        pass
    assert sub in buf.getvalue()


def test_digital_create_from_ir_file(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ir_src = Path(__file__).parent.parent / "examples" / "digital" / "tiny8_add.design.json"
    out = tmp_path / "myproj"
    monkeypatch.chdir(tmp_path)
    payload, rc = _run(
        ["digital", "create", str(ir_src), "--out", str(out), "--json"],
        capsys,
    )
    assert rc == 0
    assert payload["success"] is True
    assert payload["data"]["kind"] == "tiny8_cpu"
    assert out.is_dir()
    assert (out / "rtl" / "tiny8_cpu.v").exists()
    assert (out / "tb" / "tb_tiny8_top.v").exists()
    assert (out / "programs" / "demo.mem").exists()


def test_digital_create_from_prompt(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "fromprompt"
    monkeypatch.chdir(tmp_path)
    payload, rc = _run(
        [
            "digital",
            "create",
            "create tiny 8-bit CPU add 20 22 halt",
            "--out",
            str(out),
            "--json",
        ],
        capsys,
    )
    assert rc == 0
    assert payload["success"] is True
    assert out.is_dir()
    assert (out / "design.ir.json").exists()


def test_digital_create_with_roadmap_prompt_returns_roadmap(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload, rc = _run(["digital", "create", "buat RISC-V processor", "--json"], capsys)
    assert rc == 0
    assert payload["data"]["roadmap"] is True


def test_digital_create_with_clarification_prompt(capsys: pytest.CaptureFixture[str]) -> None:
    payload, rc = _run(
        ["digital", "create", "buat mini processor 8-bit sederhana", "--json"],
        capsys,
    )
    assert rc == 1
    assert payload["data"]["needsClarification"] is True


def test_digital_create_with_unsafe_prompt(capsys: pytest.CaptureFixture[str]) -> None:
    payload, rc = _run(["digital", "create", "rm -rf the CPU", "--json"], capsys)
    assert rc == 1
    assert payload["errors"][0]["code"] == "PROMPT_INJECTION"


def test_digital_create_missing_source(capsys: pytest.CaptureFixture[str]) -> None:
    # Empty string falls through to the planner; that yields an
    # UNSUPPORTED_PROMPT refusal, not a MISSING_SOURCE error.
    _payload, rc = _run(["digital", "create", "", "--json"], capsys)
    assert rc == 1


def test_digital_create_ir_file_not_found(capsys: pytest.CaptureFixture[str]) -> None:
    payload, rc = _run(["digital", "create", "nonexistent.design.json", "--json"], capsys)
    assert rc == 1
    assert payload["errors"][0]["code"] == "DESIGN_LOAD_FAILED"


def test_digital_assemble(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    src = tmp_path / "demo.asm"
    src.write_text("LDI 1\nHALT\n", encoding="utf-8")
    out = tmp_path / "demo.mem"
    payload, rc = _run(["digital", "assemble", str(src), "--out", str(out), "--json"], capsys)
    assert rc == 0
    assert payload["data"]["instructionCount"] == 2
    assert out.exists()
    body = out.read_text(encoding="utf-8")
    assert body.startswith("1001")  # LDI 1 -> 0x1001


def test_digital_assemble_missing_source(capsys: pytest.CaptureFixture[str]) -> None:
    payload, rc = _run(["digital", "assemble", "", "--json"], capsys)
    assert rc == 1
    assert payload["errors"][0]["code"] == "MISSING_SOURCE"


def test_digital_assemble_file_not_found(capsys: pytest.CaptureFixture[str]) -> None:
    payload, rc = _run(["digital", "assemble", "no_such_file.asm", "--json"], capsys)
    assert rc == 1
    assert payload["errors"][0]["code"] == "ASM_SOURCE_NOT_FOUND"


def test_digital_assemble_program_error(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    src = tmp_path / "bad.asm"
    src.write_text("FOO 1\n", encoding="utf-8")
    payload, rc = _run(["digital", "assemble", str(src), "--json"], capsys)
    assert rc == 1
    assert payload["errors"][0]["code"] == "ASM_UNKNOWN_OPCODE"


# ---------------------------------------------------------------------------
# Parser surface
# ---------------------------------------------------------------------------


def test_digital_help_full_text(capsys: pytest.CaptureFixture[str]) -> None:
    # argparse calls parser.exit() on --help, which raises SystemExit.
    with pytest.raises(SystemExit) as exc:
        cli_main(["digital", "--help"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    text = captured.out
    for sub in ("plan", "create", "assemble", "doctor", "simulate", "synth-check", "inspect"):
        assert sub in text, f"{sub!r} missing from `ltagent digital --help`"


def test_digital_subcommand_required(capsys: pytest.CaptureFixture[str]) -> None:
    # Bare `digital` with no subcommand should error
    with pytest.raises(SystemExit) as exc:
        cli_main(["digital"])
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# doctor / simulate / synth-check / inspect
# ---------------------------------------------------------------------------


def test_digital_doctor_lists_all_tools(capsys: pytest.CaptureFixture[str]) -> None:
    payload, rc = _run(["digital", "doctor", "--json"], capsys)
    assert rc == 0
    assert payload["success"] is True
    tools = payload["data"]["tools"]
    for tool in ("iverilog", "vvp", "verilator", "yosys", "gtkwave"):
        assert tool in tools
        assert tools[tool]["status"] in {"ok", "missing"}


def test_digital_doctor_install_hint(capsys: pytest.CaptureFixture[str]) -> None:
    payload, rc = _run(["digital", "doctor", "--json"], capsys)
    assert rc == 0
    assert "ubuntu" in payload["data"]["recommendedInstall"]


def test_digital_simulate_missing_dir(capsys: pytest.CaptureFixture[str]) -> None:
    payload, rc = _run(["digital", "simulate", "/nonexistent", "--json"], capsys)
    assert rc == 1
    assert payload["errors"][0]["code"] == "PROJECT_NOT_FOUND"


def test_digital_simulate_not_a_tiny8_project(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    (tmp_path / "rtl").mkdir()
    payload, rc = _run(["digital", "simulate", str(tmp_path), "--json"], capsys)
    assert rc == 1
    assert payload["errors"][0]["code"] == "NOT_A_TINY8_PROJECT"


def test_digital_synth_check_missing_dir(capsys: pytest.CaptureFixture[str]) -> None:
    payload, rc = _run(["digital", "synth-check", "/nonexistent", "--json"], capsys)
    assert rc == 1
    assert payload["errors"][0]["code"] == "PROJECT_NOT_FOUND"


def test_digital_inspect_returns_manifest(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # Create a minimal Tiny8 project on disk
    ir_src = Path(__file__).parent.parent / "examples" / "digital" / "tiny8_add.design.json"
    _create_payload, create_rc = _run(
        ["digital", "create", str(ir_src), "--out", str(tmp_path), "--json"],
        capsys,
    )
    assert create_rc == 0
    payload, rc = _run(["digital", "inspect", str(tmp_path), "--json"], capsys)
    assert rc == 0
    assert payload["success"] is True
    assert payload["data"]["manifest"]["designKind"] == "tiny8_cpu"
    assert "rtl/tiny8_alu.v" in payload["data"]["manifest"]["rtlFiles"]
    assert "design" in payload["data"]
