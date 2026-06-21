"""CLI tests for Phase 4 subcommands: ``parse-log`` and ``result``.

These tests run the full subprocess pipeline (``python -m ltagent``) so
the argparse wiring and output contract are exercised end-to-end.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ltagent import cli


def _python_module_invoke(
    args: list[str], env: dict[str, str] | None = None, cwd: Path | None = None
) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "ltagent", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        cwd=str(cwd) if cwd else None,
        check=False,
    )


LOGS = Path(__file__).resolve().parent / "fixtures" / "logs"


# ---------------------------------------------------------------------------
# parse-log
# ---------------------------------------------------------------------------


def test_parse_log_from_file_ok(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    proc = _python_module_invoke(
        ["parse-log", str(LOGS / "rc_lowpass_tran_ok.log"), "--json"],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["command"] == "parse-log"
    assert payload["success"] is True
    meas = payload["data"]["measurements"]
    assert "vout_max" in meas
    assert meas["vout_max"]["value"] == pytest.approx(0.70710678)
    assert meas["vout_max"]["function"] == "MAX"
    assert payload["data"]["isSimulationSuccess"] is True
    assert payload["errors"] == []


def test_parse_log_from_file_with_fatal(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    proc = _python_module_invoke(
        ["parse-log", str(LOGS / "simulation_fatal.log"), "--json"],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["command"] == "parse-log"
    assert payload["success"] is False
    codes = {e["code"] for e in payload["errors"]}
    assert "LTSPICE_FATAL" in codes
    assert payload["data"]["isSimulationSuccess"] is False


def test_parse_log_with_text_argument(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    proc = _python_module_invoke(
        [
            "parse-log",
            "--log-text",
            "vout_max: MAX(v(out))=0.70710678 FROM 0 TO 0.005\nElapsed time: 0.01 seconds.\n",
            "--json",
        ],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert "vout_max" in payload["data"]["measurements"]


def test_parse_log_rejects_missing_file(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    proc = _python_module_invoke(
        ["parse-log", str(tmp_path / "no_such_file.log"), "--json"],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["command"] == "parse-log"
    assert any(e["code"] == "LOG_FILE_NOT_FOUND" for e in payload["errors"])


def test_parse_log_rejects_both_path_and_text(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    proc = _python_module_invoke(
        [
            "parse-log",
            str(LOGS / "rc_lowpass_tran_ok.log"),
            "--log-text",
            "x",
            "--json",
        ],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["errors"][0]["code"] == "PARSE_LOG_USAGE"


def test_parse_log_help_lists_subcommand() -> None:
    proc = _python_module_invoke(["--help"])
    assert "parse-log" in proc.stdout


# ---------------------------------------------------------------------------
# result
# ---------------------------------------------------------------------------


def test_result_writes_result_json(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    out = work / "result.json"
    proc = _python_module_invoke(
        [
            "result",
            str(LOGS / "rc_lowpass_tran_ok.log"),
            "--project-id",
            "rc_lowpass_1khz",
            "--out",
            str(out),
            "--template",
            "rc_lowpass",
            "--layout-score",
            "92",
            "--json",
        ],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.is_file()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == "0.1"
    assert payload["success"] is True
    assert payload["projectId"] == "rc_lowpass_1khz"
    assert payload["measurements"]["vout_max"] == pytest.approx(0.70710678)
    assert payload["template"]["used"] == "rc_lowpass"
    assert payload["layout"]["score"] == 92
    # Always-on assertions.
    names = {a["name"] for a in payload["assertions"]}
    assert "simulation_has_no_errors" in names
    assert "simulation_finished" in names


def test_result_marks_fatal_log_as_not_success(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    out = work / "result.json"
    proc = _python_module_invoke(
        [
            "result",
            str(LOGS / "simulation_fatal.log"),
            "--project-id",
            "broken",
            "--out",
            str(out),
            "--json",
        ],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 1
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["success"] is False
    codes = {e["code"] for e in payload["errors"]}
    assert "LTSPICE_FATAL" in codes


def test_result_merges_run_payload(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    proc = _python_module_invoke(
        [
            "result",
            str(LOGS / "rc_lowpass_tran_ok.log"),
            "--project-id",
            "p",
            "--run-payload",
            json.dumps(
                {
                    "success": True,
                    "exitCode": 0,
                    "durationMs": 100,
                    "timeoutSeconds": 30,
                }
            ),
            "--json",
        ],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["data"]["result"]["run"]["exitCode"] == 0
    assert payload["data"]["result"]["run"]["durationMs"] == 100


def test_result_rejects_invalid_run_payload(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    proc = _python_module_invoke(
        [
            "result",
            str(LOGS / "rc_lowpass_tran_ok.log"),
            "--project-id",
            "p",
            "--run-payload",
            "{not json",
            "--json",
        ],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert any(e["code"] == "RESULT_RUN_PAYLOAD_INVALID" for e in payload["errors"])


def test_result_requires_project_id(tmp_path: Path) -> None:
    (tmp_path / "xdg").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    proc = _python_module_invoke(
        [
            "result",
            str(LOGS / "rc_lowpass_tran_ok.log"),
            "--json",
        ],
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        cwd=work,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert any(e["code"] == "RESULT_PROJECT_ID_REQUIRED" for e in payload["errors"])


def test_result_help_lists_subcommand() -> None:
    proc = _python_module_invoke(["--help"])
    assert "result" in proc.stdout


# ---------------------------------------------------------------------------
# in-process main() smoke
# ---------------------------------------------------------------------------


def test_main_parse_log_in_process() -> None:
    rc = cli.main(["parse-log", str(LOGS / "rc_lowpass_tran_ok.log"), "--json"])
    assert rc == 0


def test_main_result_in_process(tmp_path: Path) -> None:
    out = tmp_path / "r.json"
    rc = cli.main(
        [
            "result",
            str(LOGS / "rc_lowpass_tran_ok.log"),
            "--project-id",
            "p",
            "--out",
            str(out),
            "--json",
        ]
    )
    assert rc == 0
    assert out.is_file()
