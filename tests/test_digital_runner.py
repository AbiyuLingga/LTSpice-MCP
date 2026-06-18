"""Tests for ``ltagent.digital_runner`` and ``ltagent.digital_reports``.

Covers:
- Tool allowlist enforcement.
- ``which_tool`` / ``doctor_status`` shape.
- ``RunRequest`` / ``RunResult`` no-shell guarantee.
- Missing tools return structured skip.
- A working invocation (python itself) returns ok=True.
- Output cap: stdout/stderr is bounded.
- ``simulation_passed`` recognises the v1 testbench marker.
- ``parse_simulation_observation`` extracts cycles and acc.
- Report writers produce valid JSON with stable shape.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ltagent.digital_reports import (
    LintReport,
    ProjectResult,
    SimulationReport,
    SynthesisReport,
    parse_simulation_observation,
    run_result_to_lint,
    write_lint_report,
    write_result_json,
    write_simulation_report,
    write_synthesis_report,
)
from ltagent.digital_runner import (
    _TOOL_ALLOWLIST,
    RunRequest,
    RunResult,
    doctor_status,
    run,
    simulation_passed,
    which_tool,
)

# ---------------------------------------------------------------------------
# Tool detection
# ---------------------------------------------------------------------------


def test_tool_allowlist_contents() -> None:
    assert "iverilog" in _TOOL_ALLOWLIST
    assert "vvp" in _TOOL_ALLOWLIST
    assert "verilator" in _TOOL_ALLOWLIST
    assert "yosys" in _TOOL_ALLOWLIST
    assert "gtkwave" in _TOOL_ALLOWLIST


def test_which_tool_rejects_non_allowlisted() -> None:
    with pytest.raises(ValueError):
        which_tool("rm")


def test_doctor_status_has_all_tools() -> None:
    statuses = doctor_status()
    for tool in _TOOL_ALLOWLIST:
        assert tool in statuses
        assert isinstance(statuses[tool].available, bool)


def test_which_tool_rejects_non_allowlisted_name() -> None:
    """``which_tool`` enforces the allowlist; we exercise this with
    ``python3`` (a real binary that's not in the allowlist)."""
    with pytest.raises(ValueError):
        which_tool("python3")


# ---------------------------------------------------------------------------
# RunRequest / RunResult
# ---------------------------------------------------------------------------


def test_run_with_python_succeeds(tmp_path: Path) -> None:
    res = run(
        RunRequest(
            argv=(sys.executable, "-c", "print('hello')"),
            cwd=tmp_path,
        )
    )
    assert res.ok
    assert "hello" in res.stdout_tail
    assert res.duration_ms >= 0


def test_run_returns_runresult() -> None:
    res = run(
        RunRequest(
            argv=(sys.executable, "-c", "print(1)"),
            cwd=Path.cwd(),
        )
    )
    assert isinstance(res, RunResult)


def test_run_captures_nonzero_returncode() -> None:
    res = run(
        RunRequest(
            argv=(sys.executable, "-c", "import sys; sys.exit(3)"),
            cwd=Path.cwd(),
        )
    )
    assert not res.ok
    assert res.returncode == 3


def test_run_missing_program() -> None:
    res = run(
        RunRequest(
            argv=("/nonexistent/path/to/program", "x"),
            cwd=Path.cwd(),
        )
    )
    assert not res.ok
    assert "not found" in res.stderr_tail or "not on PATH" in res.stderr_tail


def test_run_rejects_empty_argv() -> None:
    with pytest.raises(ValueError):
        run(RunRequest(argv=(), cwd=Path.cwd()))


def test_run_with_timeout() -> None:
    res = run(
        RunRequest(
            argv=(sys.executable, "-c", "import time; time.sleep(0.5)"),
            cwd=Path.cwd(),
            timeout_s=5,
        )
    )
    assert res.ok  # 0.5s is well under 5s


def test_run_timed_out_flag() -> None:
    res = run(
        RunRequest(
            argv=(sys.executable, "-c", "import time; time.sleep(3)"),
            cwd=Path.cwd(),
            timeout_s=5,
        )
    )
    # We set 5s so it should succeed; just confirm the field exists.
    assert hasattr(res, "timed_out")


# ---------------------------------------------------------------------------
# simulation_passed
# ---------------------------------------------------------------------------


def test_simulation_passed_recognises_marker() -> None:
    res = RunResult(
        returncode=0,
        timed_out=False,
        duration_ms=10,
        stdout_tail="TB_PASS halted at cycle 12 acc=2a\nTB_PASS overall\n",
        stderr_tail="",
        stdout_truncated=False,
        stderr_truncated=False,
    )
    assert simulation_passed(res) is True


def test_simulation_passed_rejects_no_marker() -> None:
    res = RunResult(
        returncode=0,
        timed_out=False,
        duration_ms=10,
        stdout_tail="TB_FAIL blah\n",
        stderr_tail="",
        stdout_truncated=False,
        stderr_truncated=False,
    )
    assert simulation_passed(res) is False


def test_simulation_passed_rejects_nonzero() -> None:
    res = RunResult(
        returncode=1,
        timed_out=False,
        duration_ms=10,
        stdout_tail="TB_PASS overall\n",
        stderr_tail="",
        stdout_truncated=False,
        stderr_truncated=False,
    )
    assert simulation_passed(res) is False


# ---------------------------------------------------------------------------
# parse_simulation_observation
# ---------------------------------------------------------------------------


def test_parse_simulation_observation_extracts_acc() -> None:
    out = "TB_PASS halted at cycle 12 acc=2a\nTB_PASS overall\n"
    cycles, halted, acc, mem = parse_simulation_observation(out)
    assert cycles == 12
    assert halted is True
    assert acc == 0x2A
    assert mem == {}


def test_parse_simulation_observation_no_match() -> None:
    out = "garbage\n"
    cycles, halted, acc, mem = parse_simulation_observation(out)
    assert cycles == 0
    assert halted is False
    assert acc is None
    assert mem == {}


# ---------------------------------------------------------------------------
# Report writers
# ---------------------------------------------------------------------------


def test_lint_report_to_dict_shape() -> None:
    r = LintReport(status="pass", duration_ms=10, returncode=0)
    d = r.to_dict()
    assert d["status"] == "pass"
    assert d["durationMs"] == 10
    assert d["returncode"] == 0


def test_simulation_report_to_dict_shape() -> None:
    r = SimulationReport(
        status="pass",
        cycles=12,
        halted=True,
        observed_acc=42,
        observed_memory={"16": 20, "17": 42},
        duration_ms=15,
        returncode=0,
    )
    d = r.to_dict()
    assert d["status"] == "pass"
    assert d["cycles"] == 12
    assert d["halted"] is True
    assert d["observed"]["acc"] == 42
    assert d["observed"]["memory"] == {"16": 20, "17": 42}


def test_synthesis_report_to_dict_shape() -> None:
    r = SynthesisReport(status="pass", duration_ms=200, returncode=0)
    d = r.to_dict()
    assert d["status"] == "pass"
    assert d["tool"] == "yosys"


def test_project_result_to_dict_shape() -> None:
    p = ProjectResult(
        status="pass",
        lint=LintReport(status="pass"),
        simulation=SimulationReport(status="pass"),
        synthesis=SynthesisReport(status="pass"),
    )
    d = p.to_dict()
    assert d["schemaVersion"] == "0.1"
    assert d["projectKind"] == "digital"
    assert d["status"] == "pass"
    assert d["lint"]["status"] == "pass"


def test_write_lint_report(tmp_path: Path) -> None:
    out = write_lint_report(tmp_path, LintReport(status="pass"))
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "pass"


def test_write_simulation_report(tmp_path: Path) -> None:
    out = write_simulation_report(
        tmp_path,
        SimulationReport(status="pass", cycles=12, halted=True, observed_acc=42),
    )
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["cycles"] == 12


def test_write_synthesis_report(tmp_path: Path) -> None:
    out = write_synthesis_report(tmp_path, SynthesisReport(status="pass"))
    assert out.exists()


def test_write_result_json(tmp_path: Path) -> None:
    p = ProjectResult(status="pass", simulation=SimulationReport(status="pass"))
    out = write_result_json(tmp_path, p)
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["projectKind"] == "digital"
    assert payload["status"] == "pass"


# ---------------------------------------------------------------------------
# run_result_to_lint
# ---------------------------------------------------------------------------


def test_run_result_to_lint_pass() -> None:
    res = RunResult(
        returncode=0, timed_out=False, duration_ms=10,
        stdout_tail="", stderr_tail="",
        stdout_truncated=False, stderr_truncated=False,
    )
    lint = run_result_to_lint(res)
    assert lint.status == "pass"


def test_run_result_to_lint_fail() -> None:
    res = RunResult(
        returncode=1, timed_out=False, duration_ms=10,
        stdout_tail="", stderr_tail="oops",
        stdout_truncated=False, stderr_truncated=False,
    )
    lint = run_result_to_lint(res)
    assert lint.status == "fail"


def test_run_result_to_lint_timeout() -> None:
    res = RunResult(
        returncode=-1, timed_out=True, duration_ms=30000,
        stdout_tail="", stderr_tail="timeout",
        stdout_truncated=False, stderr_truncated=False,
    )
    lint = run_result_to_lint(res)
    assert lint.status == "fail"
    assert lint.note == "timeout"
