"""End-to-end CLI tests for ``ltagent create`` (Phase 7).

These tests run the actual ``ltagent`` entry point via ``python -m``
so the full argparse → cmd_create_safe → project orchestrator →
JSON-contract pipeline is exercised. We use isolated temp directories
for projects, templates, and CWD so the suite never touches the real
workspace.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run_ltagent(
    args: list[str], cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """Invoke ``ltagent`` via ``python -m ltagent`` and capture output."""
    cmd = [sys.executable, "-m", "ltagent", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        cwd=str(cwd),
        check=False,
    )


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated workspace root with ``projects`` and ``templates`` dirs."""
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "projects").mkdir()
    (cwd / "templates").mkdir()
    monkeypatch.chdir(cwd)
    # Force a clean config search: the test must not pick up any
    # user-level config file from the host.
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return cwd


# --- happy path -----------------------------------------------------------


def test_create_from_ir_json_writes_project(workspace: Path, examples_dir: Path) -> None:
    proc = _run_ltagent(
        [
            "create",
            str(examples_dir / "rc_lowpass.ir.json"),
            "--json",
        ],
        cwd=workspace,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["success"] is True
    assert payload["command"] == "create"
    assert payload["data"]["irKind"] == "ir_file"
    # The 5 standard artifacts are present in the project dir.
    target = Path(payload["data"]["target"])
    assert target.is_dir()
    for name in (
        "circuit.ir.json",
        "circuit.cir",
        "circuit.asc",
        "result.json",
        "metadata.json",
    ):
        assert (target / name).is_file(), f"missing {name}"


def test_create_from_ir_json_returns_json_shape(workspace: Path, examples_dir: Path) -> None:
    proc = _run_ltagent(
        ["create", str(examples_dir / "rc_lowpass.ir.json"), "--json"],
        cwd=workspace,
    )
    payload = json.loads(proc.stdout)
    for key in ("success", "command", "message", "data", "warnings", "errors"):
        assert key in payload
    assert payload["errors"] == []
    assert payload["data"]["run"]["status"] == "not_requested"
    assert payload["data"]["layout"]["score"] is not None


def test_create_from_prompt_uses_planner(workspace: Path) -> None:
    proc = _run_ltagent(
        ["create", "buat RC low-pass cutoff 1kHz dengan C 100nF", "--json"],
        cwd=workspace,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["success"] is True
    assert payload["data"]["irKind"] == "prompt"
    target = Path(payload["data"]["target"])
    assert (target / "circuit.ir.json").is_file()
    # The IR's name should be derived from the prompt.
    ir = json.loads((target / "circuit.ir.json").read_text())
    assert ir["topology"] == "rc_lowpass"


def test_create_with_run_flag_but_no_ltspice_reports_failure(
    workspace: Path, examples_dir: Path
) -> None:
    proc = _run_ltagent(
        [
            "create",
            str(examples_dir / "rc_lowpass.ir.json"),
            "--run",
            "--json",
        ],
        cwd=workspace,
    )
    # Project is still created; the CLI does not flip success for
    # run-level failures (the project itself succeeded).
    payload = json.loads(proc.stdout)
    assert payload["success"] is True
    assert payload["data"]["run"]["status"] == "attempted"
    assert payload["data"]["run"]["success"] is False
    # A warning codes LTSPICE_UNAVAILABLE is surfaced.
    codes = [w.get("code") for w in payload.get("warnings", [])]
    assert "LTSPICE_UNAVAILABLE" in codes


def test_create_with_explicit_out_overrides_default(workspace: Path, examples_dir: Path) -> None:
    explicit = workspace / "my_project"
    proc = _run_ltagent(
        [
            "create",
            str(examples_dir / "rc_lowpass.ir.json"),
            "--out",
            str(explicit),
            "--json",
        ],
        cwd=workspace,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert Path(payload["data"]["target"]) == explicit
    assert (explicit / "circuit.ir.json").is_file()


# --- error paths ----------------------------------------------------------


def test_create_rejects_existing_non_empty_dir(workspace: Path, examples_dir: Path) -> None:
    target = workspace / "projects" / "preexisting"
    target.mkdir(parents=True)
    (target / "junk.txt").write_text("keep me", encoding="utf-8")
    proc = _run_ltagent(
        [
            "create",
            str(examples_dir / "rc_lowpass.ir.json"),
            "--out",
            str(target),
            "--json",
        ],
        cwd=workspace,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["success"] is False
    assert any(e["code"] == "PROJECT_TARGET_NOT_EMPTY" for e in payload["errors"])
    # Pre-existing content is preserved.
    assert (target / "junk.txt").read_text() == "keep me"


def test_create_rejects_traversal_outside_workspace(workspace: Path, examples_dir: Path) -> None:
    outside = workspace.parent / "evil_target"
    proc = _run_ltagent(
        [
            "create",
            str(examples_dir / "rc_lowpass.ir.json"),
            "--out",
            str(outside),
            "--json",
        ],
        cwd=workspace,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["success"] is False
    assert any(e["code"] == "PATH_OUTSIDE_WORKSPACE" for e in payload["errors"])
    # The traversal target was not created.
    assert not outside.exists()


def test_create_allows_outside_workspace_with_flag(workspace: Path, examples_dir: Path) -> None:
    outside = workspace.parent / "legit_target"
    proc = _run_ltagent(
        [
            "create",
            str(examples_dir / "rc_lowpass.ir.json"),
            "--out",
            str(outside),
            "--allow-outside-workspace",
            "--json",
        ],
        cwd=workspace,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["success"] is True
    assert (outside / "circuit.ir.json").is_file()


def test_create_rejects_unrecognized_prompt(workspace: Path) -> None:
    proc = _run_ltagent(
        ["create", "give me a complex nonlinear quantum circuit", "--json"],
        cwd=workspace,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["success"] is False
    assert "supportedTopologies" in payload["data"]
    assert "voltage_divider" in payload["data"]["supportedTopologies"]


def test_create_rejects_missing_ir_file(workspace: Path) -> None:
    proc = _run_ltagent(
        ["create", "does-not-exist.ir.json", "--json"],
        cwd=workspace,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["success"] is False


def test_create_with_both_ir_file_and_prompt_ambiguous(workspace: Path, examples_dir: Path) -> None:
    proc = _run_ltagent(
        [
            "create",
            "--ir-file",
            str(examples_dir / "rc_lowpass.ir.json"),
            "--prompt",
            "make rc low pass",
            "--json",
        ],
        cwd=workspace,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["success"] is False
    assert payload["errors"][0]["code"] == "CREATE_USAGE"


# --- templates integration ------------------------------------------------


def test_create_matches_seeded_template(workspace: Path, examples_dir: Path) -> None:
    # Seed the templates dir using the ltagent template seed command.
    seed_proc = _run_ltagent(["template", "seed", "--json"], cwd=workspace)
    assert seed_proc.returncode == 0, seed_proc.stderr

    proc = _run_ltagent(
        ["create", str(examples_dir / "rc_lowpass.ir.json"), "--json"],
        cwd=workspace,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["success"] is True
    assert payload["data"]["template"]["used"] == "rc_lowpass"
    # The on-disk metadata.json must reflect the same template id.
    md = json.loads((Path(payload["data"]["target"]) / "metadata.json").read_text())
    assert md["template"]["used"] == "rc_lowpass"


def test_create_auto_seeds_default_templates_on_first_run(
    workspace: Path, examples_dir: Path
) -> None:
    """A fresh ``ltagent create`` in an empty workspace auto-seeds the
    bundled official library, so the project matches against the
    full template catalogue instead of falling through to a
    ``TEMPLATE_NOT_FOUND`` warning.

    The previous behaviour surfaced a "run ``ltagent template seed``"
    hint; the new default is to seed transparently on the first read
    path. The explicit ``ltagent template seed`` command is still
    available for callers that want the manual form.
    """
    proc = _run_ltagent(
        ["create", str(examples_dir / "rc_lowpass.ir.json"), "--json"],
        cwd=workspace,
    )
    payload = json.loads(proc.stdout)
    assert payload["success"] is True
    # The rc_lowpass official template was matched (auto-seeded on
    # the way in); no TEMPLATE_NOT_FOUND hint is surfaced.
    assert payload["data"]["template"]["used"] == "rc_lowpass"
    codes = [w.get("code") for w in payload.get("warnings", [])]
    assert "TEMPLATE_NOT_FOUND" not in codes
    # And the templates directory now contains the seeded official
    # templates, so subsequent CLI invocations are no-ops.
    list_proc = _run_ltagent(
        ["template", "list", "--status", "official", "--json"],
        cwd=workspace,
    )
    list_payload = json.loads(list_proc.stdout)
    assert list_payload["data"]["count"] == 10


# --- --help ---------------------------------------------------------------


def test_create_help_lists_subcommand(workspace: Path) -> None:
    proc = _run_ltagent(["create", "--help"], cwd=workspace)
    assert proc.returncode == 0
    assert "create" in proc.stdout
    for opt in ("--ir-file", "--prompt", "--out", "--run", "--templates-dir"):
        assert opt in proc.stdout, f"--help missing {opt}"
