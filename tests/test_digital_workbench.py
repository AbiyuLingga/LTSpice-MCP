"""Tests for the digital workbench.

The host this test runs on does not have iverilog / yosys
installed, so the tests are split:

* ``test_digital_design_emits_verilog`` and friends exercise
  the deterministic Verilog-2001 generator.
* ``test_run_simulation_skipped_when_iverilog_missing`` exercises
  the structured skip path.
* ``test_run_simulation_with_user_supplied_tool`` runs a
  stand-in shell script that pretends to be iverilog.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ltagent.digital_workbench import (
    DIGITAL_DESIGN_SCHEMA_VERSION,
    IVERILOG_TOOL_ID,
    SUPPORTED_VERILOG_KINDS,
    ClockSpec,
    DigitalDesignIR,
    DigitalModule,
    DigitalWorkbenchError,
    Port,
    ResetSpec,
    TestGoal,
    design_to_verilog,
    run_simulation,
    run_synthesis,
)
from ltagent.jobs import JobState


def _counter_design() -> DigitalDesignIR:
    return DigitalDesignIR(
        schemaVersion=DIGITAL_DESIGN_SCHEMA_VERSION,
        topModule="counter8",
        clock=ClockSpec(name="clk", periodNs=10),
        reset=ResetSpec(name="rst_n", active="low"),
        modules=[
            DigitalModule(
                name="counter8",
                kind="counter",
                ports=[
                    Port(name="clk", direction="in", width=1),
                    Port(name="rst_n", direction="in", width=1),
                    Port(name="q", direction="out", width=8),
                ],
                body="reg [7:0] q;\nalways @(posedge clk) q <= rst_n ? 8'h0 : q + 8'h1;",
            )
        ],
        testGoals=[TestGoal(name="q_increments", expression="q == 8'h0A", timeoutCycles=20)],
    )


def test_digital_design_ir_round_trip() -> None:
    design = _counter_design()
    payload = design.model_dump(mode="json")
    assert payload["schemaVersion"] == DIGITAL_DESIGN_SCHEMA_VERSION
    assert payload["topModule"] == "counter8"
    assert payload["clock"]["name"] == "clk"
    assert payload["reset"]["active"] == "low"


def test_verilog_emits_module_testbench_and_clock() -> None:
    text = design_to_verilog(_counter_design())
    assert "module counter8(" in text
    assert "input clk" in text
    assert "input rst_n" in text
    assert "output [7:0] q" in text
    assert "module tb_counter8;" in text
    assert "always #(5) clk" in text


def test_verilog_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        DigitalModule(name="bad", kind="rocket")


def test_verilog_rejects_unknown_top_module() -> None:
    design = _counter_design().model_copy(update={"topModule": "ghost"})
    with pytest.raises(DigitalWorkbenchError) as captured:
        design_to_verilog(design)
    assert captured.value.code == "WORKBENCH_DIGITAL_TOPOLOGY_INVALID"


def test_supported_kinds_match_doc() -> None:
    assert "counter" in SUPPORTED_VERILOG_KINDS
    assert "fsm" in SUPPORTED_VERILOG_KINDS
    assert "pwm" in SUPPORTED_VERILOG_KINDS


def test_run_simulation_skipped_when_iverilog_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "ltagent.digital_workbench.discover_tool", lambda tool_id: None
    )
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    result = run_simulation("proj", project_dir, _counter_design())
    assert result.skipped_reason is not None
    assert result.bundle.status == "skipped"
    assert result.manifest.state == JobState.SKIPPED


def test_run_simulation_with_user_supplied_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_tool = tmp_path / "fake_iverilog.sh"
    fake_tool.write_text(
        "#!/bin/sh\n"
        "touch \"$3\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_tool.chmod(0o755)
    monkeypatch.setattr(
        "ltagent.digital_workbench.discover_tool",
        lambda tool_id: __import__("ltagent.digital_workbench", fromlist=["DigitalToolInfo"]).DigitalToolInfo(
            toolId=tool_id, executable=fake_tool, version="fake"
        )
        if tool_id == IVERILOG_TOOL_ID
        else None,
    )
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    result = run_simulation("proj", project_dir, _counter_design())
    assert result.bundle.status == "success"
    run_dirs = [p for p in (project_dir / "runs").iterdir() if p.is_dir()]
    assert run_dirs
    assert (run_dirs[0] / "counter8.v").is_file()


def test_run_synthesis_skipped_when_yosys_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "ltagent.digital_workbench.discover_tool", lambda tool_id: None
    )
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    result = run_synthesis("proj", project_dir, _counter_design())
    assert result.bundle.status == "skipped"


def test_run_simulation_with_failing_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_tool = tmp_path / "fail.sh"
    fake_tool.write_text("#!/bin/sh\necho broken 1>&2\nexit 7\n", encoding="utf-8")
    fake_tool.chmod(0o755)
    monkeypatch.setattr(
        "ltagent.digital_workbench.discover_tool",
        lambda tool_id: __import__("ltagent.digital_workbench", fromlist=["DigitalToolInfo"]).DigitalToolInfo(
            toolId=tool_id, executable=fake_tool, version="fake"
        ),
    )
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    result = run_simulation("proj", project_dir, _counter_design())
    assert result.bundle.status == "failed"
    assert result.manifest.state == JobState.FAILED
