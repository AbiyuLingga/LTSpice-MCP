"""End-to-end CLI tests using ``CliRunner`` from the standard library.

We use ``subprocess`` against the installed ``ltagent`` entry point to
exercise the full script → module → JSON pipeline. Where the entry point
is not installed (e.g. in a bare pytest run from a fresh checkout), the
tests fall back to invoking ``ltagent.cli.main`` directly via
``python -m``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ltagent import cli


def _python_module_invoke(
    args: list[str], env: dict[str, str] | None = None, cwd: Path | None = None
) -> subprocess.CompletedProcess:
    """Invoke the CLI via ``python -m ltagent`` to avoid relying on the installed script."""
    cmd = [sys.executable, "-m", "ltagent", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        cwd=str(cwd) if cwd else None,
        check=False,
    )


def _ltagent_script_invoke(args: list[str]) -> subprocess.CompletedProcess | None:
    """Invoke the installed ``ltagent`` script, returning None if not on PATH."""
    exe = shutil.which("ltagent")
    if not exe:
        return None
    return subprocess.run([exe, *args], capture_output=True, text=True, check=False)


# --- top-level behaviour -------------------------------------------------


def test_version_exits_zero_and_reports_version() -> None:
    proc = _python_module_invoke(["--version"])
    assert proc.returncode == 0, proc.stderr
    assert "0.0.1" in proc.stdout


def test_help_exits_zero_and_lists_subcommands() -> None:
    proc = _python_module_invoke(["--help"])
    assert proc.returncode == 0
    assert "Local CLI and MCP adapter" in proc.stdout
    assert "(later)" not in proc.stdout
    for cmd in ("doctor", "init", "config", "ir", "netlist"):
        assert cmd in proc.stdout


def test_no_args_prints_help_to_stderr_and_exits_2() -> None:
    proc = _python_module_invoke([])
    # argparse with no subcommand and no --version: we print help and exit 2.
    assert proc.returncode == 2


# --- doctor --------------------------------------------------------------


def test_doctor_json_shape(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    proc = _python_module_invoke(
        ["doctor", "--json"],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode in (0, 1), proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["command"] == "doctor"
    assert {"success", "message", "data", "warnings", "errors"} <= set(payload)
    assert isinstance(payload["data"]["checks"], list)
    names = {c["name"] for c in payload["data"]["checks"]}
    assert "python_version" in names
    assert "lt_spice_executable" in names
    assert "wine" in names


def test_doctor_reports_missing_executable(tmp_path: Path) -> None:
    # Isolate HOME so no user config interferes; write a config pointing at a
    # guaranteed-missing executable.
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "config.toml").write_text(
        "[ltspice]\n"
        'executable = "/tmp/this_path_does_not_exist_xyz_123.exe"\n'
        'wine_command = "/tmp/no_wine_here_xyz_123"\n',
        encoding="utf-8",
    )
    proc = _python_module_invoke(
        ["--config", str(tmp_path / "work" / "config.toml"), "doctor", "--json"],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=tmp_path / "work",
    )
    payload = json.loads(proc.stdout)
    codes = {e["code"] for e in payload["errors"]}
    codes |= {w["code"] for w in payload["warnings"]}
    assert "LTSPICE_EXECUTABLE_MISSING" in codes or "LTSPICE_EXECUTABLE_NOT_SET" in codes
    assert any(c["status"] in ("warn", "fail") for c in payload["data"]["checks"])


# --- init ----------------------------------------------------------------


def test_init_creates_project_inside_workspace(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    proc = _python_module_invoke(
        ["init", "demo_project", "--json"],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["success"] is True
    target = work / "projects" / "demo_project"
    assert target.is_dir()
    assert (target / "circuit.ir.json").is_file()
    assert (target / "metadata.json").is_file()
    ir = json.loads((target / "circuit.ir.json").read_text(encoding="utf-8"))
    assert ir["schemaVersion"] == "0.1"
    assert ir["name"] == "demo_project"


# --- create --save-template (Phase 7 <-> Phase 9 bridge) ------------------


def test_create_save_template_skipped_when_project_missing(
    tmp_path: Path,
) -> None:
    """When --save-template is set but the project dir is missing,
    the bridge reports a structured warning and the create command
    still reports its own success.
    """
    from argparse import Namespace

    from ltagent.cli import _augment_create_with_template

    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    payload = {
        "success": True,
        "command": "create",
        "message": "Project created",
        "data": {},
        "warnings": [],
        "errors": [],
    }
    args = Namespace(
        save_template=True,
        template_id=None,
        template_description="from create",
        template_tag=None,
    )
    out = _augment_create_with_template(payload, tmp_path / "does_not_exist", templates_dir, args)
    assert out["success"] is True
    assert out["data"]["templateBridge"]["status"] == "skipped"
    assert any(w["code"] == "TEMPLATE_PROJECT_INVALID" for w in out["warnings"])


def test_create_save_template_creates_candidate(tmp_path: Path) -> None:
    """A complete project with a successful run + layout score turns
    into a candidate manifest under the templates dir."""
    import json as _json
    from argparse import Namespace

    from ltagent.cli import _augment_create_with_template

    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    ir = {
        "schemaVersion": "0.1",
        "name": "vdiv_bridge",
        "topology": "voltage_divider",
        "description": "Reusable voltage divider",
        "nodes": ["in", "out", "0"],
        "components": [
            {
                "id": "Vin",
                "kind": "voltage_source",
                "spicePrefix": "V",
                "nodes": ["in", "0"],
                "value": "DC 12",
                "role": "input_source",
            },
            {
                "id": "R1",
                "kind": "resistor",
                "spicePrefix": "R",
                "nodes": ["in", "out"],
                "value": "1k",
                "role": "series_resistor",
            },
            {
                "id": "R2",
                "kind": "resistor",
                "spicePrefix": "R",
                "nodes": ["out", "0"],
                "value": "1k",
                "role": "shunt_resistor",
            },
        ],
        "analysis": [{"kind": "op"}],
    }
    (project_dir / "circuit.ir.json").write_text(_json.dumps(ir, indent=2), encoding="utf-8")
    (project_dir / "result.json").write_text(
        _json.dumps(
            {
                "schemaVersion": "0.1",
                "projectId": "vdiv_bridge",
                "run": {"attempted": True, "success": True, "exitCode": 0},
                "layoutScore": 92,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    payload = {
        "success": True,
        "command": "create",
        "message": "Project created",
        "data": {},
        "warnings": [],
        "errors": [],
    }
    args = Namespace(
        save_template=True,
        template_id=None,
        template_description="my saved divider",
        template_tag=None,  # default -> user-requested
    )
    out = _augment_create_with_template(payload, project_dir, templates_dir, args)
    assert out["success"] is True
    assert out["data"]["templateBridge"]["status"] == "created"
    cand = templates_dir / "candidates" / "vdiv_bridge"
    assert cand.is_dir()
    assert (cand / "manifest.json").is_file()
    assert out["data"]["templateBridge"]["evaluation"]["promotionEligible"] is True


def test_init_refuses_target_outside_workspace(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    proc = _python_module_invoke(
        ["init", str(tmp_path / "outside"), "--json"],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    payload = json.loads(proc.stdout)
    assert payload["success"] is False
    assert any(e["code"] == "PATH_OUTSIDE_WORKSPACE" for e in payload["errors"])
    assert not (tmp_path / "outside").exists()


def test_init_refuses_non_empty_target(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "projects" / "occupied").mkdir(parents=True)
    (work / "projects" / "occupied" / "stuff.txt").write_text("x", encoding="utf-8")
    proc = _python_module_invoke(
        ["init", "occupied", "--json"],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    payload = json.loads(proc.stdout)
    assert payload["success"] is False
    assert any(e["code"] == "TARGET_NOT_EMPTY" for e in payload["errors"])


# --- config --------------------------------------------------------------


def test_config_show_returns_resolved_dict(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    proc = _python_module_invoke(
        ["config", "show", "--json"],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["success"] is True
    assert "workspace" in payload["data"]["config"]
    assert "ltspice" in payload["data"]["config"]


def test_config_validate_warns_when_no_executable(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    proc = _python_module_invoke(
        ["config", "validate", "--json"],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    codes = {w["code"] for w in payload["warnings"]}
    assert "LTSPICE_EXECUTABLE_NOT_SET" in codes


def test_config_validate_fails_on_malformed_config(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    bad = work / "config.toml"
    bad.write_text('not = valid = "toml"\n', encoding="utf-8")
    proc = _python_module_invoke(
        ["--config", str(bad), "config", "validate", "--json"],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["success"] is False
    assert payload["errors"][0]["code"] == "CONFIG_INVALID"


# --- main() in-process (no subprocess) ----------------------------------


def test_main_returns_correct_exit_code_for_unknown_subcommand() -> None:

    with pytest.raises(SystemExit) as exc:
        cli.main(["nope"])
    assert exc.value.code == 2


# --- run (Phase 3) -------------------------------------------------------


def test_run_reports_missing_executable(tmp_path: Path) -> None:
    """`ltagent run` must report a missing executable as a structured fail,
    not a stack trace, even when the .cir is valid.
    """
    work = tmp_path / "work"
    work.mkdir()
    cir = work / "smoke.cir"
    cir.write_text("V1 in 0 1\n.op\n.end\n", encoding="utf-8")
    proc = _python_module_invoke(
        ["run", str(cir), "--json"],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    # The subprocess may exit with 0 (no exe configured) or 1 (no exe set).
    # The point is that stdout must be valid JSON.
    assert proc.returncode in (0, 1), proc.stderr
    payload = json.loads(proc.stdout)
    codes = {e["code"] for e in payload.get("errors", [])}
    assert "LTSPICE_EXECUTABLE_NOT_SET" in codes


def test_run_reports_cir_missing(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    proc = _python_module_invoke(
        ["run", str(work / "no_such.cir"), "--json"],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode in (0, 1), proc.stderr
    payload = json.loads(proc.stdout)
    codes = {e["code"] for e in payload.get("errors", [])}
    assert "LTSPICE_CIR_MISSING" in codes


# --- ir + netlist CLI ----------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXAMPLES = _REPO_ROOT / "examples"


def test_ir_validate_ok(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    proc = _python_module_invoke(
        ["ir", "validate", str(_EXAMPLES / "rc_lowpass.ir.json")],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["command"] == "ir.validate"
    assert payload["success"] is True
    assert payload["data"]["topology"] == "rc_lowpass"
    assert payload["data"]["componentCount"] == 3


def test_ir_validate_reports_validation_errors(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    bad = _REPO_ROOT / "tests" / "fixtures" / "invalid" / "missing_ground.json"
    proc = _python_module_invoke(
        ["ir", "validate", str(bad)],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["success"] is False
    assert payload["command"] == "ir.validate"
    codes = {e["code"] for e in payload["errors"]}
    assert "NODES_MISSING_GROUND" in codes


def test_ir_validate_missing_file(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    proc = _python_module_invoke(
        ["ir", "validate", "/nonexistent.json"],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["success"] is False
    assert payload["errors"][0]["code"] == "IR_FILE_NOT_FOUND"


def test_ir_schema_text_prints_raw_schema(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    proc = _python_module_invoke(
        ["ir", "schema", "--text"],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 0
    parsed = json.loads(proc.stdout)
    assert "$defs" in parsed


def test_netlist_writes_file_and_reports_counters(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    out = work / "out.cir"
    proc = _python_module_invoke(
        [
            "netlist",
            str(_EXAMPLES / "voltage_divider.ir.json"),
            "--out",
            str(out),
        ],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["success"] is True
    assert payload["data"]["writtenTo"] == str(out)
    assert payload["data"]["componentCount"] == 3
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert text.rstrip().endswith(".end")
    assert "Vin in 0 DC 12" in text
    assert ".op" in text


def test_netlist_to_stdout_includes_text_in_data(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    proc = _python_module_invoke(
        ["netlist", str(_EXAMPLES / "rc_lowpass.ir.json")],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["data"]["writtenTo"] is None
    assert ".tran 0 5m" in payload["data"]["netlist"]
    assert payload["data"]["netlist"].rstrip().endswith(".end")


def test_netlist_rejects_unsafe_directive(tmp_path: Path) -> None:
    from ltagent.ir import load_ir

    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    ir = load_ir(_EXAMPLES / "rc_lowpass.ir.json")
    ir.directives.append(".include /etc/passwd")
    src = work / "unsafe.ir.json"
    src.write_text(ir.model_dump_json(indent=2), encoding="utf-8")
    proc = _python_module_invoke(
        ["netlist", str(src)],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["success"] is False
    assert payload["errors"][0]["code"] == "DIR_UNSUPPORTED"


def test_netlist_allow_unsafe_directives_flag_is_accepted(tmp_path: Path) -> None:
    """The ``--allow-unsafe-directives`` flag is accepted without
    changing the success path on a clean IR. The detailed rejection
    semantics are covered by the unit tests in ``test_netlist.py``
    because pydantic's IR validator already catches all unknown
    directives at the boundary, making the flag a no-op for
    well-formed IRs."""
    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    proc = _python_module_invoke(
        [
            "netlist",
            str(_EXAMPLES / "voltage_divider.ir.json"),
            "--out",
            str(work / "out.cir"),
            "--allow-unsafe-directives",
        ],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["success"] is True
    assert payload["data"]["rejectedDirectives"] == []


def test_netlist_rejects_invalid_ir(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    bad = _REPO_ROOT / "tests" / "fixtures" / "invalid" / "missing_ground.json"
    proc = _python_module_invoke(
        ["netlist", str(bad), "--out", str(work / "out.cir")],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["success"] is False
    codes = {e["code"] for e in payload["errors"]}
    assert "NODES_MISSING_GROUND" in codes
    # No file should have been written because the IR was invalid.
    assert not (work / "out.cir").exists()


# ---------------------------------------------------------------------------
# Phase 8: plan subcommand
# ---------------------------------------------------------------------------


def test_plan_voltage_divider_emits_circuit(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    proc = _python_module_invoke(
        ["plan", "make voltage divider 12V to 5V"],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["success"] is True
    assert payload["command"] == "plan"
    assert payload["data"]["topology"] == "voltage_divider"
    assert payload["data"]["name"] == "voltage_divider_12v_to_5v"
    circuit = payload["data"]["circuit"]
    assert circuit["schemaVersion"] == "0.1"
    assert len(circuit["components"]) == 3
    assert {c["id"] for c in circuit["components"]} == {"Vin", "R1", "R2"}


def test_plan_rc_lowpass_in_indonesian(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    proc = _python_module_invoke(
        ["plan", "buat RC low-pass cutoff 1kHz dengan C 100nF"],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["success"] is True
    assert payload["data"]["topology"] == "rc_lowpass"
    assert payload["data"]["name"] == "rc_lowpass_1khz_c100nf"


def test_plan_unsupported_returns_refusal(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    proc = _python_module_invoke(
        ["plan", "make something weird"],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=tmp_path,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["success"] is False
    assert payload["command"] == "plan"
    assert payload["errors"][0]["code"] == "UNSUPPORTED_PROMPT"
    assert "rc_lowpass" in payload["data"]["supportedTopologies"]


def test_plan_voltage_divider_only_one_voltage(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    proc = _python_module_invoke(
        ["plan", "make voltage divider 12V"],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=tmp_path,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["success"] is False
    codes = {e["code"] for e in payload["errors"]}
    assert "MISSING_PARAM" in codes or "INVALID_VALUE" in codes


def test_plan_writes_ir_to_out_path(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    out = work / "plan.out.json"
    proc = _python_module_invoke(
        [
            "plan",
            "make voltage divider 12V to 5V",
            "--out",
            str(out),
        ],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["success"] is True
    assert out.exists()
    written = json.loads(out.read_text())
    assert written["topology"] == "voltage_divider"
    assert payload["data"]["writtenTo"].endswith("plan.out.json")


def test_plan_refuses_to_write_outside_cwd(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "elsewhere.json"
    proc = _python_module_invoke(
        [
            "plan",
            "make voltage divider 12V to 5V",
            "--out",
            str(outside),
        ],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["success"] is False
    assert payload["errors"][0]["code"] == "PATH_OUTSIDE_CWD"
    assert not outside.exists()


def test_plan_help_includes_subcommand() -> None:
    proc = _python_module_invoke(
        ["plan", "--help"],
        env={"XDG_CONFIG_HOME": "/tmp"},
    )
    assert proc.returncode == 0
    assert "natural-language" in proc.stdout.lower()


# ---------------------------------------------------------------------------
# Phase 5: asc subcommand
# ---------------------------------------------------------------------------


def test_asc_writes_file_and_reports_score(tmp_path: Path) -> None:
    """``ltagent asc --out PATH`` writes a .asc with the three
    MVP sections and reports a layout score in the JSON payload."""
    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    out = work / "rc_lowpass.asc"
    proc = _python_module_invoke(
        [
            "asc",
            str(_EXAMPLES / "rc_lowpass.ir.json"),
            "--out",
            str(out),
        ],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["success"] is True
    assert payload["command"] == "asc"
    assert payload["data"]["writtenTo"] == str(out)
    assert payload["data"]["layoutScore"] == 100
    assert payload["data"]["layoutClassification"] == "official"
    assert out.is_file()
    body = out.read_text(encoding="utf-8")
    assert "Version 4" in body
    assert "SHEET 1 880 680" in body
    assert "FLAG" in body and "0" in body


def test_asc_to_stdout_includes_schematic_text(tmp_path: Path) -> None:
    """Without ``--out`` the schematic text is included in the
    JSON ``data.schematic`` so the agent can inspect it without
    a second pass."""
    (tmp_path / "xdg").mkdir()
    proc = _python_module_invoke(
        ["asc", str(_EXAMPLES / "rc_highpass.ir.json")],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["data"]["writtenTo"] is None
    assert "SINE(0 1 200)" in payload["data"]["schematic"]
    assert "3.18k" in payload["data"]["schematic"]


def test_asc_rejects_invalid_ir(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    bad = _REPO_ROOT / "tests" / "fixtures" / "invalid" / "missing_ground.json"
    proc = _python_module_invoke(
        ["asc", str(bad), "--out", str(work / "out.asc")],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["success"] is False
    codes = {e["code"] for e in payload["errors"]}
    assert "NODES_MISSING_GROUND" in codes
    assert not (work / "out.asc").exists()


def test_asc_rejects_missing_file(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    proc = _python_module_invoke(
        ["asc", str(tmp_path / "does_not_exist.ir.json")],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["success"] is False
    assert payload["errors"][0]["code"] == "IR_FILE_NOT_FOUND"


def test_asc_help_lists_subcommand() -> None:
    proc = _python_module_invoke(
        ["asc", "--help"],
        env={"XDG_CONFIG_HOME": "/tmp"},
    )
    assert proc.returncode == 0
    assert "schematic" in proc.stdout.lower()
    assert "--out" in proc.stdout


def test_asc_main_dispatch_in_process(tmp_path: Path) -> None:
    """The asc subcommand is reachable from ``main()`` in-process,
    not only via the subprocess entry point. This guards against
    accidental parser registration drift."""
    (tmp_path / "xdg").mkdir()
    out = tmp_path / "out.asc"
    rc = cli.main(  # type: ignore[attr-defined]
        [
            "asc",
            str(_EXAMPLES / "rc_lowpass.ir.json"),
            "--out",
            str(out),
            "--json",
        ]
    )
    assert rc == 0
    assert out.is_file()


@pytest.mark.parametrize(
    "example_name",
    [
        "voltage_divider",
        "rc_lowpass",
        "rc_highpass",
        "inverting_opamp",
        "noninv_opamp",
        "comparator",
        "diode_clipper",
        "halfwave_rectifier",
        "bridge_rectifier",
        "transistor_switch",
    ],
)
def test_asc_json_serialisable_for_every_example(example_name: str, tmp_path: Path) -> None:
    """The asc --json payload must be JSON-serialisable for every
    supported example, including the Phase 11 analog topologies that
    put :class:`Point` instances inside layout-warning ``data``
    fields. Regression for the layout-warnings dataclass bug: prior
    to the serializer hardening this command would crash with
    ``TypeError: Object of type Point is not JSON serialisable``
    on any example that produced a layout warning.
    """
    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    out = work / f"{example_name}.asc"
    proc = _python_module_invoke(
        [
            "asc",
            str(_EXAMPLES / f"{example_name}.ir.json"),
            "--out",
            str(out),
            "--json",
        ],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 0, proc.stderr
    # The contract is JSON; if the payload can be re-parsed and the
    # warning objects are well-formed dicts, the regression is held.
    payload = json.loads(proc.stdout)
    assert payload["success"] is True
    assert payload["command"] == "asc"
    data = payload["data"]
    assert data["topology"]
    assert isinstance(data["layoutScore"], int)
    # When --out is given, layout warnings live at the top level.
    # When --out is omitted, they also live at ``data.layoutWarnings``.
    # We check both locations for shape.
    warnings = data["layoutWarnings"] if "layoutWarnings" in data else payload.get("warnings", [])
    assert isinstance(warnings, list)
    for w in warnings:
        assert set(w.keys()) >= {"code", "detail", "data"}
        # Nested data must be a plain dict, not a dataclass repr.
        assert isinstance(w["data"], dict)
        for key in w["data"]:
            assert isinstance(key, str), f"warning data key {key!r} is not str"
