"""Tests for the sandboxed ngspice subprocess adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from ltagent.ngspice_runner import (
    NgspiceError,
    NgspiceRequest,
    build_ngspice_argv,
    run_ngspice,
)


def _netlist(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    netlist = workspace / "filter.cir"
    netlist.write_text("V1 in 0 1\nR1 in out 1k\nC1 out 0 100n\n.end\n", encoding="utf-8")
    return workspace, netlist


def test_build_ngspice_argv_is_batch_only_and_workspace_confined(tmp_path: Path) -> None:
    workspace, netlist = _netlist(tmp_path)
    request = NgspiceRequest(netlist=netlist, workspace=workspace)

    argv = build_ngspice_argv(request)

    assert argv == (
        "ngspice",
        "-b",
        "-o",
        str(workspace / "runs" / "filter.log"),
        "-r",
        str(workspace / "runs" / "filter.raw"),
        str(netlist),
    )


def test_build_ngspice_argv_rejects_a_netlist_outside_workspace(tmp_path: Path) -> None:
    workspace, _netlist_path = _netlist(tmp_path)
    outside = tmp_path / "outside.cir"
    outside.write_text(".end\n", encoding="utf-8")

    with pytest.raises(NgspiceError) as excinfo:
        build_ngspice_argv(NgspiceRequest(netlist=outside, workspace=workspace))

    assert excinfo.value.code == "PATH_TRAVERSAL"


def test_run_ngspice_returns_structured_skip_when_tool_is_not_installed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, netlist = _netlist(tmp_path)
    monkeypatch.setattr("ltagent.ngspice_runner.shutil.which", lambda _name: None)

    result = run_ngspice(NgspiceRequest(netlist=netlist, workspace=workspace))

    assert result.status == "skipped"
    assert result.code == "NGSPICE_MISSING"
    assert result.run is None
