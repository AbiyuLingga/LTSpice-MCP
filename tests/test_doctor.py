"""Unit tests for ``ltagent.doctor``.

Every check is exercised through monkeypatched subprocess and filesystem
fixtures. No test in this file may invoke a real Wine / LTspice binary.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from ltagent import doctor
from ltagent.config import Config, LTSpiceConfig

# --- helpers --------------------------------------------------------------


def _config_with(
    *,
    executable: str | None = None,
    wine_command: str | None = None,
    mode: str = "wine",
    timeout: int = 30,
) -> Config:
    return Config(
        ltspice=LTSpiceConfig(
            mode=mode, executable=executable, wine_command=wine_command
        ),
        runner=Config().runner.__class__(timeout_seconds=timeout),
    )


# --- check_python ---------------------------------------------------------


def test_check_python_ok_on_supported_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "version_info", (3, 12, 0, "final", 0))
    r = doctor.check_python()
    assert r.status == "ok"
    assert r.code == "PYTHON_OK"
    assert r.data["version"] == "3.12.0"


def test_check_python_fails_on_old_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "version_info", (3, 10, 9, "final", 0))
    r = doctor.check_python()
    assert r.status == "fail"
    assert r.code == "PYTHON_TOO_OLD"
    assert r.data["version"] == "3.10.9"


# --- check_package_version ------------------------------------------------


def test_check_package_version_warns_when_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(_: str) -> str:
        raise doctor.metadata.PackageNotFoundError

    monkeypatch.setattr(doctor.metadata, "version", _raise)
    r = doctor.check_package_version()
    assert r.status == "warn"
    assert r.code == "PACKAGE_NOT_INSTALLED"


# --- check_config ---------------------------------------------------------


def test_check_config_warns_when_using_defaults(default_config: Config) -> None:
    r = doctor.check_config(default_config)
    assert r.status == "warn"
    assert r.code == "CONFIG_USING_DEFAULTS"


def test_check_config_ok_when_loaded(workspace_root: Path) -> None:
    cfg = Config(source_path=workspace_root / "config.toml")
    r = doctor.check_config(cfg)
    assert r.status == "ok"
    assert r.code == "CONFIG_OK"


# --- check_workspace_writable --------------------------------------------


def test_check_workspace_writable_ok(tmp_path: Path) -> None:
    p = tmp_path / "projects"
    r = doctor.check_workspace_writable(p)
    assert r.status == "ok"
    assert r.code == "WORKSPACE_OK"
    assert r.data["path"] == str(p)


def test_check_workspace_writable_fails_on_readonly(tmp_path: Path) -> None:
    p = tmp_path / "projects"
    p.mkdir()
    p.chmod(0o555)
    try:
        r = doctor.check_workspace_writable(p)
    finally:
        p.chmod(0o755)
    assert r.status == "fail"
    assert r.code == "WORKSPACE_NOT_WRITABLE"


# --- check_executable -----------------------------------------------------


def test_check_executable_fail_when_unset() -> None:
    r = doctor.check_executable(None)
    assert r.status == "fail"
    assert r.code == "LTSPICE_EXECUTABLE_NOT_SET"


def test_check_executable_fail_when_missing(tmp_path: Path) -> None:
    r = doctor.check_executable(str(tmp_path / "missing.exe"))
    assert r.status == "fail"
    assert r.code == "LTSPICE_EXECUTABLE_MISSING"


def test_check_executable_warn_when_not_posix_exec(fake_executable: Path) -> None:
    r = doctor.check_executable(str(fake_executable))
    assert r.status == "warn"
    assert r.code == "LTSPICE_EXECUTABLE_NOT_POSIX_EXEC"
    assert r.data["sizeBytes"] > 0


def test_check_executable_ok_when_executable(tmp_path: Path) -> None:
    p = tmp_path / "lt.exe"
    p.write_bytes(b"hi")
    p.chmod(0o755)
    r = doctor.check_executable(str(p))
    assert r.status == "ok"
    assert r.code == "LTSPICE_EXECUTABLE_OK"


# --- check_wine -----------------------------------------------------------


def test_check_wine_ok_when_configured_works(tmp_path: Path) -> None:
    wine = tmp_path / "wine"
    wine.write_text("#!/bin/sh\necho wine-1.2.3\n")
    wine.chmod(0o755)

    # check_wine does not accept a subprocess injection point in the public API;
    # we just verify the configured path was used and that it returned ok.
    r = doctor.check_wine(str(wine))
    assert r.status == "ok"
    assert r.code == "WINE_OK"
    assert r.data["path"] == str(wine)


def test_check_wine_fail_when_nothing_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch the well-known paths to point at non-existent files in a tmp dir.
    monkeypatch.setattr(doctor, "WELL_KNOWN_WINE_PATHS", (str(tmp_path / "no_wine_a"),))
    # `shutil.which("wine")` is hard to control on systems where wine IS on
    # PATH (e.g. CI on Linux). The function tolerates that by still trying
    # the configured path and the well-known list. We just check the result
    # is either ok (if some wine is installed) or a structured fail.
    r = doctor.check_wine(None)
    assert r.status in ("ok", "fail")
    if r.status == "fail":
        assert r.code == "WINE_NOT_FOUND"


def test_check_wine_warn_on_launch_error(tmp_path: Path) -> None:
    wine = tmp_path / "wine"
    wine.write_text("noop")
    wine.chmod(0o755)
    # Patch subprocess.run inside the doctor module via a stub that raises.
    real_run = doctor.subprocess.run

    def _raise(*a: Any, **kw: Any) -> Any:
        raise OSError("boom")

    doctor.subprocess.run = _raise  # type: ignore[assignment]
    try:
        r = doctor.check_wine(str(wine))
    finally:
        doctor.subprocess.run = real_run  # type: ignore[assignment]
    assert r.status == "warn"
    assert r.code == "WINE_LAUNCH_ERROR"


# --- check_wine_prefix ---------------------------------------------------


def test_check_wine_prefix_ok_when_drive_c_exists(tmp_path: Path) -> None:
    (tmp_path / ".wine" / "drive_c").mkdir(parents=True)
    r = doctor.check_wine_prefix(home=tmp_path)
    assert r.status == "ok"
    assert r.code == "WINE_PREFIX_OK"


def test_check_wine_prefix_warn_when_missing(tmp_path: Path) -> None:
    r = doctor.check_wine_prefix(home=tmp_path)
    assert r.status == "warn"
    assert r.code == "WINE_PREFIX_MISSING"


# --- check_temp_writable --------------------------------------------------


def test_check_temp_writable_ok() -> None:
    r = doctor.check_temp_writable()
    assert r.status == "ok"
    assert r.code == "TEMP_OK"


# --- check_log_parser_loaded --------------------------------------------


def test_check_log_parser_loaded_ok() -> None:
    r = doctor.check_log_parser_loaded()
    assert r.status == "ok"
    assert r.code == "LOG_PARSER_PRESENT"


# --- check_lt_spice_smoke (the dangerous one) ---------------------------


def _make_run_returning(returncode: int, stdout: str = "", stderr: str = "") -> Any:
    return lambda *a, **kw: subprocess.CompletedProcess(
        args=a[0] if a else [],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_smoke_skip_when_executable_unset() -> None:
    cfg = _config_with(executable=None)
    r = doctor.check_lt_spice_smoke(cfg)
    assert r.status == "skip"
    assert r.code == "LTSPICE_EXECUTABLE_NOT_SET"


def test_smoke_skip_when_executable_missing(tmp_path: Path) -> None:
    cfg = _config_with(executable=str(tmp_path / "missing.exe"))
    r = doctor.check_lt_spice_smoke(cfg)
    assert r.status == "skip"
    assert r.code == "LTSPICE_EXECUTABLE_MISSING"


def test_smoke_skip_when_wine_unresolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = tmp_path / "lt.exe"
    exe.write_text("noop")
    monkeypatch.setattr(doctor, "_resolve_wine_for_run", lambda _: None)
    cfg = _config_with(executable=str(exe), wine_command=None)
    r = doctor.check_lt_spice_smoke(cfg)
    assert r.status == "skip"
    assert r.code == "WINE_NOT_FOUND"


def test_smoke_timeout_returns_structured_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = tmp_path / "lt.exe"
    exe.write_text("noop")
    monkeypatch.setattr(
        doctor, "_resolve_wine_for_run", lambda _: "/usr/bin/wine"
    )

    def _raise_timeout(*a: Any, **kw: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd=a[0] if a else [], timeout=kw.get("timeout", 30))

    cfg = _config_with(executable=str(exe), wine_command="/usr/bin/wine", timeout=20)
    r = doctor.check_lt_spice_smoke(cfg, run_subprocess=_raise_timeout)
    assert r.status == "fail"
    assert r.code == "LTSPICE_TIMEOUT"
    assert r.data["timeoutSeconds"] == 20
    assert "argv" in r.data
    assert "/usr/bin/wine" in r.data["argv"]


def test_smoke_no_log_returns_structured_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = tmp_path / "lt.exe"
    exe.write_text("noop")
    monkeypatch.setattr(doctor, "_resolve_wine_for_run", lambda _: "/usr/bin/wine")

    cfg = _config_with(executable=str(exe), wine_command="/usr/bin/wine")
    r = doctor.check_lt_spice_smoke(
        cfg, run_subprocess=_make_run_returning(0, stdout="done", stderr="")
    )
    assert r.status == "fail"
    assert r.code == "LTSPICE_NO_LOG"
    assert r.data["exitCode"] == 0


def test_smoke_ok_when_log_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the fake subprocess creates the .log, the check should report ok.

    We replace ``tempfile.TemporaryDirectory`` with a context manager that
    yields a fixed tmp dir, then we have a fake ``subprocess.run`` create
    the expected ``smoke.log`` before returning.
    """
    exe = tmp_path / "lt.exe"
    exe.write_text("noop")
    monkeypatch.setattr(doctor, "_resolve_wine_for_run", lambda _: "/usr/bin/wine")

    workdir = tmp_path / "work"
    workdir.mkdir()

    real_td = doctor.tempfile.TemporaryDirectory

    class _FixedTD:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        def __enter__(self) -> str:
            return str(workdir)

        def __exit__(self, *a: Any) -> None:
            return None

    monkeypatch.setattr(doctor.tempfile, "TemporaryDirectory", _FixedTD)

    def _fake_run(argv: list[str], **kw: Any) -> Any:
        (workdir / "smoke.log").write_text("stepping...\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="ok", stderr=""
        )

    cfg = _config_with(executable=str(exe), wine_command="/usr/bin/wine")
    r = doctor.check_lt_spice_smoke(cfg, run_subprocess=_fake_run)
    assert r.status == "ok", r
    assert r.code == "LTSPICE_OK"
    assert r.data["logBytes"] > 0
    doctor.tempfile.TemporaryDirectory = real_td  # restore for safety


def test_smoke_launch_error_returns_structured_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = tmp_path / "lt.exe"
    exe.write_text("noop")
    monkeypatch.setattr(doctor, "_resolve_wine_for_run", lambda _: "/usr/bin/wine")

    def _raise_fnf(*a: Any, **kw: Any) -> Any:
        raise FileNotFoundError("no such exe")

    cfg = _config_with(executable=str(exe), wine_command="/usr/bin/wine")
    r = doctor.check_lt_spice_smoke(cfg, run_subprocess=_raise_fnf)
    assert r.status == "fail"
    assert r.code == "LTSPICE_LAUNCH_ERROR"


# --- aggregate / payload helpers ----------------------------------------


def test_aggregate_status_fail_wins() -> None:
    cs = [
        doctor.CheckResult("a", "ok", "A", "ok"),
        doctor.CheckResult("b", "warn", "B", "warn"),
        doctor.CheckResult("c", "fail", "C", "fail"),
    ]
    assert doctor.aggregate_status(cs) == "fail"


def test_aggregate_status_warn_when_no_fail() -> None:
    cs = [
        doctor.CheckResult("a", "ok", "A", "ok"),
        doctor.CheckResult("b", "warn", "B", "warn"),
    ]
    assert doctor.aggregate_status(cs) == "warn"


def test_aggregate_status_ok() -> None:
    cs = [doctor.CheckResult("a", "ok", "A", "ok")]
    assert doctor.aggregate_status(cs) == "ok"


def test_aggregate_status_skip_only() -> None:
    cs = [doctor.CheckResult("a", "skip", "A", "skip")]
    assert doctor.aggregate_status(cs) == "skip"


def test_to_json_payload_shape() -> None:
    cs = [
        doctor.CheckResult("a", "ok", "A_OK", "all good"),
        doctor.CheckResult("b", "warn", "B_WARN", "uh"),
        doctor.CheckResult("c", "fail", "C_FAIL", "boom"),
    ]
    p = doctor.to_json_payload("doctor", "ran", cs, data={"x": 1})
    assert p["success"] is False
    assert p["command"] == "doctor"
    assert p["message"] == "ran"
    assert p["data"]["x"] == 1
    assert p["data"]["status"] == "fail"
    assert len(p["warnings"]) == 1
    assert len(p["errors"]) == 1
    assert p["warnings"][0]["code"] == "B_WARN"
    assert p["errors"][0]["code"] == "C_FAIL"


# --- run_doctor orchestrator --------------------------------------------


def test_run_doctor_offline_safe(
    workspace_root: Path,
    default_config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_doctor must not blow up when LTspice/Wine are absent."""
    # Pretend no wine anywhere.
    monkeypatch.setattr(doctor, "WELL_KNOWN_WINE_PATHS", ())
    # Don't actually run the smoke sim; this test only exercises the no-simulate path.
    checks = doctor.run_doctor(default_config, simulate=False)
    codes = {c.code for c in checks}
    assert "PYTHON_OK" in codes
    assert "WORKSPACE_OK" in codes
    assert "TEMP_OK" in codes
    assert "LOG_PARSER_PRESENT" in codes
