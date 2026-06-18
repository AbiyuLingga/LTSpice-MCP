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
    "args",
    [
        ["digital", "create", "tiny8.design.json", "--json"],
        ["digital", "assemble", "demo.asm", "--json"],
        ["digital", "doctor", "--json"],
        ["digital", "simulate", "projects/foo", "--json"],
        ["digital", "synth-check", "projects/foo", "--json"],
        ["digital", "inspect", "projects/foo", "--json"],
    ],
)
def test_digital_stub_subcommands(
    args: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    payload, rc = _run(args, capsys)
    # All stubs return success=true with a DIGITAL_NOT_IMPLEMENTED warning.
    # The plan says the parser is complete from day one; the warning is
    # the structured "real impl is in a later phase" signal.
    assert rc == 0
    assert payload["success"] is True
    assert payload["warnings"][0]["code"] == "DIGITAL_NOT_IMPLEMENTED"
    assert payload["data"]["phase"] == 12


# ---------------------------------------------------------------------------
# Parser surface
# ---------------------------------------------------------------------------


def test_digital_help_lists_all_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
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
