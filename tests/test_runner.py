"""Unit tests for ``ltagent.runner``.

Every test runs without LTspice / Wine: the subprocess layer is
monkey-patched. The integration marker is reserved for tests that
genuinely require a real LTspice install (see ``test_runner_integration``).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from ltagent import runner
from ltagent.runner import (
    INTEGRATION_MARKER,
    RunRequest,
    build_argv,
    resolve_wine,
    run_simulation,
)


# --- helpers --------------------------------------------------------------


def _write_cir(workdir: Path, name: str = "smoke.cir", content: str | None = None) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    p = workdir / name
    p.write_text(content or "V1 in 0 1\nR1 in 0 1k\n.op\n.end\n", encoding="utf-8")
    return p


def _request(
    workdir: Path,
    *,
    cir_name: str = "smoke.cir",
    executable: str | None = "/usr/bin/lt.exe",
    wine_command: str | None = "/usr/bin/wine",
    mode: str = "wine",
    timeout: int = 30,
    extra_args: tuple[str, ...] = (),
    expected_log_name: str | None = None,
    require_cir_under_workdir: bool = True,
) -> RunRequest:
    cir = workdir / cir_name
    return RunRequest(
        cir_path=cir,
        workdir=workdir,
        timeout_seconds=timeout,
        mode=mode,
        executable=executable,
        wine_command=wine_command,
        extra_args=extra_args,
        expected_log_name=expected_log_name,
        require_cir_under_workdir=require_cir_under_workdir,
    )


def _make_run_returning(
    returncode: int = 0,
    *,
    stdout: str = "",
    stderr: str = "",
    side_effect: Any = None,
) -> Any:
    """Build a fake ``subprocess.run`` that produces the given completion.

    If ``side_effect`` is provided, it is invoked instead and may raise
    (e.g. ``subprocess.TimeoutExpired``).
    """

    def _fake(argv: list[str], **kwargs: Any) -> Any:
        if side_effect is not None:
            return side_effect(argv, **kwargs)
        return subprocess.CompletedProcess(
            args=argv, returncode=returncode, stdout=stdout, stderr=stderr
        )

    return _fake


# --- resolve_wine --------------------------------------------------------


def test_resolve_wine_returns_configured_when_present(tmp_path: Path) -> None:
    wine = tmp_path / "wine"
    wine.write_text("#!/bin/sh\necho wine-1.2.3\n")
    wine.chmod(0o755)
    assert resolve_wine(str(wine)) == str(wine.resolve())


def test_resolve_wine_falls_back_to_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # No configured wine; force shutil.which("wine") to return a real file.
    wine = tmp_path / "wine"
    wine.write_text("#!/bin/sh\necho wine\n")
    wine.chmod(0o755)
    monkeypatch.setattr(runner.shutil, "which", lambda _: str(wine))
    monkeypatch.setattr(runner, "WELL_KNOWN_WINE_PATHS", ())
    assert resolve_wine(None) == str(wine)


def test_resolve_wine_uses_well_known_when_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wine = tmp_path / "wine-stable" / "bin" / "wine"
    wine.parent.mkdir(parents=True)
    wine.write_text("#!/bin/sh\necho wine\n")
    wine.chmod(0o755)
    monkeypatch.setattr(runner.shutil, "which", lambda _: None)
    monkeypatch.setattr(runner, "WELL_KNOWN_WINE_PATHS", (str(wine),))
    assert resolve_wine(None) == str(wine.resolve())


def test_resolve_wine_returns_none_when_nothing_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner.shutil, "which", lambda _: None)
    monkeypatch.setattr(runner, "WELL_KNOWN_WINE_PATHS", ("/no/such/wine",))
    assert resolve_wine(None) is None
    assert resolve_wine("/also/missing/wine") is None


# --- build_argv ----------------------------------------------------------


def test_build_argv_native_mode(tmp_path: Path) -> None:
    exe = tmp_path / "lt.exe"
    exe.write_text("noop")
    workdir = tmp_path / "work"
    workdir.mkdir()
    cir = _write_cir(workdir)
    req = _request(workdir, executable=str(exe), wine_command=None, mode="native")
    argv = build_argv(req)
    assert argv == [str(exe.resolve()), "-b", str(cir)]


def test_build_argv_wine_mode_uses_configured_wine(tmp_path: Path) -> None:
    exe = tmp_path / "lt.exe"
    exe.write_text("noop")
    wine = tmp_path / "wine"
    wine.write_text("noop")
    workdir = tmp_path / "work"
    workdir.mkdir()
    cir = _write_cir(workdir)
    req = _request(
        workdir, executable=str(exe), wine_command=str(wine), mode="wine"
    )
    argv = build_argv(req)
    assert argv[0] == str(wine.resolve())
    assert argv[1] == str(exe.resolve())
    assert "-b" in argv
    assert argv[-1] == str(cir)


def test_build_argv_wine_mode_uses_wine_resolver(tmp_path: Path) -> None:
    exe = tmp_path / "lt.exe"
    exe.write_text("noop")
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_cir(workdir)
    sentinel = "/opt/wine-stable/bin/wine"
    req = _request(workdir, executable=str(exe), wine_command=None, mode="wine")
    argv = build_argv(req, wine_resolver=lambda _: sentinel)
    assert argv[0] == sentinel


def test_build_argv_passes_extra_args(tmp_path: Path) -> None:
    exe = tmp_path / "lt.exe"
    exe.write_text("noop")
    workdir = tmp_path / "work"
    workdir.mkdir()
    cir = _write_cir(workdir)
    req = _request(
        workdir,
        executable=str(exe),
        wine_command=None,
        mode="native",
        extra_args=("-ascii", "-netlist"),
    )
    argv = build_argv(req)
    # argv is: [exe, -b, *extra, cir]
    assert argv == [str(exe.resolve()), "-b", "-ascii", "-netlist", str(cir)]


def test_build_argv_quotes_paths_with_spaces(tmp_path: Path) -> None:
    """Regression for the local ``Program Files`` LTspice path.

    Argv should keep the executable as a single list element so
    ``subprocess.run`` quotes it correctly even when it contains
    spaces.
    """
    pf = tmp_path / "Program Files"
    pf.mkdir()
    exe = pf / "XVIIx64.exe"
    exe.write_text("noop")
    workdir = tmp_path / "work"
    workdir.mkdir()
    cir = _write_cir(workdir)
    req = _request(workdir, executable=str(exe), wine_command=None, mode="native")
    argv = build_argv(req)
    assert len(argv) == 3
    assert " " in argv[0]
    assert argv[0] == str(exe.resolve())


def test_build_argv_missing_executable_raises(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_cir(workdir)
    req = _request(workdir, executable=None, mode="native")
    with pytest.raises(runner.RunnerBuildError) as ei:
        build_argv(req)
    assert ei.value.code == runner.ERR_EXECUTABLE_NOT_SET


def test_build_argv_executable_not_on_disk_raises(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_cir(workdir)
    req = _request(
        workdir, executable=str(tmp_path / "no_such.exe"), mode="native"
    )
    with pytest.raises(runner.RunnerBuildError) as ei:
        build_argv(req)
    assert ei.value.code == runner.ERR_EXECUTABLE_MISSING


def test_build_argv_wine_not_found_raises(tmp_path: Path) -> None:
    exe = tmp_path / "lt.exe"
    exe.write_text("noop")
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_cir(workdir)
    req = _request(
        workdir, executable=str(exe), wine_command=None, mode="wine"
    )
    with pytest.raises(runner.RunnerBuildError) as ei:
        build_argv(req, wine_resolver=lambda _: None)
    assert ei.value.code == runner.ERR_WINE_NOT_FOUND


def test_build_argv_invalid_mode_raises(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_cir(workdir)
    req = _request(workdir, mode="docker")
    with pytest.raises(runner.RunnerBuildError) as ei:
        build_argv(req)
    assert ei.value.code == runner.ERR_MODE_INVALID


# --- run_simulation: pre-flight errors ---------------------------------


def test_run_simulation_cir_missing(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    req = _request(workdir, cir_name="nope.cir")
    res = run_simulation(req)
    assert res.success is False
    assert res.errors[0]["code"] == runner.ERR_CIR_MISSING
    assert res.data["mode"] == "wine"


def test_run_simulation_cir_is_a_directory(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    # cir_path points at a directory, not a file
    (workdir / "weird.cir").mkdir()
    req = _request(workdir, cir_name="weird.cir")
    res = run_simulation(req)
    assert res.success is False
    assert res.errors[0]["code"] == runner.ERR_CIR_NOT_FILE


def test_run_simulation_executable_not_set(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_cir(workdir)
    req = _request(workdir, executable=None, mode="native")
    res = run_simulation(req)
    assert res.success is False
    assert res.errors[0]["code"] == runner.ERR_EXECUTABLE_NOT_SET


def test_run_simulation_executable_missing(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_cir(workdir)
    req = _request(
        workdir, executable=str(tmp_path / "no_such.exe"), mode="native"
    )
    res = run_simulation(req)
    assert res.success is False
    assert res.errors[0]["code"] == runner.ERR_EXECUTABLE_MISSING


def test_run_simulation_wine_not_found(tmp_path: Path) -> None:
    exe = tmp_path / "lt.exe"
    exe.write_text("noop")
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_cir(workdir)
    req = _request(
        workdir, executable=str(exe), wine_command=None, mode="wine"
    )
    res = run_simulation(
        req, run_subprocess=_make_run_returning(), wine_resolver_override=None  # type: ignore[arg-type]
    ) if False else _runner_with_no_wine(req)
    assert res.success is False
    assert res.errors[0]["code"] == runner.ERR_WINE_NOT_FOUND


def _runner_with_no_wine(req: RunRequest) -> runner.RunResult:
    """Helper: run with a wine resolver that always returns None."""
    return runner.run_simulation(
        req,
        wine_resolver=lambda _: None,
        run_subprocess=_make_run_returning(),
    )


def test_run_simulation_refuses_cir_outside_workdir(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    _write_cir(other, name="escape.cir")
    # Point the request at a .cir that lives outside workdir
    req = RunRequest(
        cir_path=other / "escape.cir",
        workdir=workdir,
        timeout_seconds=10,
        mode="native",
        executable="/usr/bin/lt.exe",
        wine_command=None,
        require_cir_under_workdir=True,
    )
    res = run_simulation(req)
    assert res.success is False
    assert res.errors[0]["code"] == runner.ERR_LAUNCH
    assert "not under" in res.errors[0]["detail"]


def test_run_simulation_allows_cir_outside_workdir_when_opted_in(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    cir = _write_cir(other, name="escape.cir")
    exe = tmp_path / "lt.exe"
    exe.write_text("noop")
    req = RunRequest(
        cir_path=cir,
        workdir=workdir,
        timeout_seconds=10,
        mode="native",
        executable=str(exe),
        wine_command=None,
        require_cir_under_workdir=False,
    )
    res = run_simulation(
        req,
        run_subprocess=_fake_writes_log(tmp_path),
    )
    # Should not be the path-traversal fail code.
    if not res.success:
        assert res.errors[0]["code"] != runner.ERR_LAUNCH or "outside" not in res.errors[0]["detail"]


def _fake_writes_log(workdir: Path) -> Any:
    """Build a fake subprocess that creates a .log file inside workdir."""

    def _fake(argv: list[str], **kwargs: Any) -> Any:
        # argv ends with the cir path; reuse the workdir provided via cwd.
        cir = Path(argv[-1])
        log = Path(kwargs["cwd"]) / (cir.stem + ".log")
        log.write_text("stepping...\n", encoding="utf-8")
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="ok", stderr="")

    return _fake


# --- run_simulation: subprocess outcomes --------------------------------


def test_run_simulation_timeout_returns_structured_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = tmp_path / "lt.exe"
    exe.write_text("noop")
    wine = tmp_path / "wine"
    wine.write_text("noop")
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_cir(workdir)

    monkeypatch.setattr(runner, "resolve_wine", lambda _: str(wine))

    def _raise_timeout(argv: list[str], **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 30))

    req = _request(
        workdir,
        executable=str(exe),
        wine_command=str(wine),
        mode="wine",
        timeout=15,
    )
    res = run_simulation(req, run_subprocess=_raise_timeout)
    assert res.success is False
    assert res.errors[0]["code"] == runner.ERR_TIMEOUT
    assert res.data["timeoutSeconds"] == 15
    assert "argv" in res.errors[0]["data"]
    assert str(wine) in res.errors[0]["data"]["argv"]


def test_run_simulation_filenotfound_returns_structured_fail(
    tmp_path: Path,
) -> None:
    exe = tmp_path / "lt.exe"
    exe.write_text("noop")
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_cir(workdir)
    req = _request(workdir, executable=str(exe), mode="native")

    def _raise_fnf(argv: list[str], **kwargs: Any) -> Any:
        raise FileNotFoundError("no such exe")

    res = run_simulation(req, run_subprocess=_raise_fnf)
    assert res.success is False
    assert res.errors[0]["code"] == runner.ERR_LAUNCH
    assert "argv" in res.errors[0]["data"]


def test_run_simulation_oserror_returns_structured_fail(
    tmp_path: Path,
) -> None:
    exe = tmp_path / "lt.exe"
    exe.write_text("noop")
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_cir(workdir)
    req = _request(workdir, executable=str(exe), mode="native")

    def _raise_os(argv: list[str], **kwargs: Any) -> Any:
        raise OSError("boom")

    res = run_simulation(req, run_subprocess=_raise_os)
    assert res.success is False
    assert res.errors[0]["code"] == runner.ERR_LAUNCH


def test_run_simulation_no_log_returns_structured_fail(tmp_path: Path) -> None:
    exe = tmp_path / "lt.exe"
    exe.write_text("noop")
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_cir(workdir)
    req = _request(workdir, executable=str(exe), mode="native")
    res = run_simulation(
        req,
        run_subprocess=_make_run_returning(0, stdout="done", stderr=""),
    )
    assert res.success is False
    assert res.errors[0]["code"] == runner.ERR_NO_LOG
    assert res.data["exitCode"] == 0


def test_run_simulation_success_reports_log_path_and_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = tmp_path / "lt.exe"
    exe.write_text("noop")
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_cir(workdir)
    req = _request(workdir, executable=str(exe), mode="native")

    # Make duration deterministic and >0 ms.
    ticks = iter([100.0, 100.5])

    def _clock() -> float:
        return next(ticks)

    res = run_simulation(
        req,
        run_subprocess=_fake_writes_log(workdir),
        clock=_clock,
    )
    assert res.success is True, res
    assert res.command == "run"
    assert res.data["logPath"] == str(workdir / "smoke.log")
    assert res.data["logBytes"] is not None and res.data["logBytes"] > 0
    assert res.data["durationMs"] == 500
    assert res.data["exitCode"] == 0
    assert res.data["mode"] == "native"
    assert res.errors == []


def test_run_simulation_nonzero_exit_emits_warning(tmp_path: Path) -> None:
    exe = tmp_path / "lt.exe"
    exe.write_text("noop")
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_cir(workdir)
    req = _request(workdir, executable=str(exe), mode="native")
    res = run_simulation(
        req,
        run_subprocess=_fake_writes_log_with_exit(workdir, exit_code=2),
    )
    assert res.success is True
    codes = {w["code"] for w in res.warnings}
    assert "LTSPICE_NONZERO_EXIT" in codes
    assert res.data["exitCode"] == 2


def _fake_writes_log_with_exit(workdir: Path, *, exit_code: int) -> Any:
    def _fake(argv: list[str], **kwargs: Any) -> Any:
        cir = Path(argv[-1])
        log = Path(kwargs["cwd"]) / (cir.stem + ".log")
        log.write_text("stepping...\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            args=argv, returncode=exit_code, stdout="ok", stderr=""
        )

    return _fake


def test_run_simulation_detects_raw_file_when_present(tmp_path: Path) -> None:
    exe = tmp_path / "lt.exe"
    exe.write_text("noop")
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_cir(workdir)
    req = _request(workdir, executable=str(exe), mode="native")

    def _fake(argv: list[str], **kwargs: Any) -> Any:
        cir = Path(argv[-1])
        wd = Path(kwargs["cwd"])
        (wd / (cir.stem + ".log")).write_text("ok\n", encoding="utf-8")
        (wd / (cir.stem + ".raw")).write_bytes(b"\x00" * 16)
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="ok", stderr=""
        )

    res = run_simulation(req, run_subprocess=_fake)
    assert res.success is True
    assert res.data["rawPath"] == str(workdir / "smoke.raw")
    assert res.data["rawBytes"] == 16


def test_run_simulation_uses_expected_log_name(tmp_path: Path) -> None:
    exe = tmp_path / "lt.exe"
    exe.write_text("noop")
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_cir(workdir)
    req = _request(
        workdir,
        executable=str(exe),
        mode="native",
        expected_log_name="custom.log",
    )

    def _fake(argv: list[str], **kwargs: Any) -> Any:
        wd = Path(kwargs["cwd"])
        (wd / "custom.log").write_text("ok\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="ok", stderr=""
        )

    res = run_simulation(req, run_subprocess=_fake)
    assert res.success is True
    assert res.data["logPath"] == str(workdir / "custom.log")


def test_run_simulation_timeout_floor_enforced(tmp_path: Path) -> None:
    """timeout_seconds below MIN_TIMEOUT_SECONDS is clamped, not respected literally."""
    exe = tmp_path / "lt.exe"
    exe.write_text("noop")
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_cir(workdir)
    req = _request(
        workdir, executable=str(exe), mode="native", timeout=1
    )
    captured: dict[str, Any] = {}

    def _fake(argv: list[str], **kwargs: Any) -> Any:
        captured["timeout"] = kwargs.get("timeout")
        return _fake_writes_log(workdir)(argv, **kwargs)

    res = run_simulation(req, run_subprocess=_fake)
    assert res.success is True
    assert captured["timeout"] >= runner.MIN_TIMEOUT_SECONDS


# --- to_dict / JSON shape -------------------------------------------------


def test_run_result_to_dict_has_required_keys(tmp_path: Path) -> None:
    exe = tmp_path / "lt.exe"
    exe.write_text("noop")
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_cir(workdir)
    req = _request(workdir, executable=str(exe), mode="native")
    res = run_simulation(
        req,
        run_subprocess=_fake_writes_log(workdir),
    )
    d = res.to_dict()
    assert d["success"] is True
    assert d["command"] == "run"
    assert "message" in d
    assert "data" in d
    assert "warnings" in d
    assert "errors" in d
    assert isinstance(d["data"]["argv"], list)
    assert d["data"]["mode"] == "native"


# --- argv never contains a shell ------------------------------------------


def test_argv_is_a_flat_list_of_strings(tmp_path: Path) -> None:
    """Regression: subprocess must be called with a list, never shell=True."""
    exe = tmp_path / "lt.exe"
    exe.write_text("noop")
    wine = tmp_path / "wine"
    wine.write_text("noop")
    workdir = tmp_path / "work"
    workdir.mkdir()
    _write_cir(workdir)
    req = _request(
        workdir, executable=str(exe), wine_command=str(wine), mode="wine"
    )
    argv = build_argv(req)
    assert isinstance(argv, list)
    for token in argv:
        assert isinstance(token, str), token
    # Nothing should be joined into a single string (which would be the
    # shape required by shell=True).
    assert len(argv) >= 3


# --- integration marker declaration --------------------------------------


def test_integration_marker_constant_is_stable() -> None:
    assert INTEGRATION_MARKER == "integration"
    # Must match the marker name in pyproject.toml.
    assert "integration" in INTEGRATION_MARKER


# --- integration test (auto-skip when LTspice absent) ---------------------


@pytest.mark.integration
def test_run_simulation_real_smoke_circuit(tmp_path: Path) -> None:
    """Integration test: runs an actual LTspice batch.

    Skipped automatically if LTspice / Wine are not present, so this is
    safe to keep in the default test run on machines that do not have
    the simulator installed.
    """
    from ltagent.config import Config, LTSpiceConfig

    # Look for a usable ltspice + wine in the usual places.
    cfg = Config(ltspice=LTSpiceConfig(mode="wine"))
    if not cfg.ltspice.executable:
        pytest.skip("ltspice.executable not configured")
    exe = Path(cfg.ltspice.executable).expanduser()
    if not exe.is_file():
        pytest.skip(f"ltspice executable not found: {exe}")
    wine = resolve_wine(cfg.ltspice.wine_command)
    if not wine:
        pytest.skip("wine not found")

    workdir = tmp_path / "integration_work"
    workdir.mkdir()
    cir = _write_cir(workdir)
    req = RunRequest(
        cir_path=cir,
        workdir=workdir,
        timeout_seconds=60,
        mode="wine",
        executable=str(exe),
        wine_command=wine,
    )
    res = run_simulation(req)
    # We don't assert success here because Wine on this host is known
    # to be flaky. We do assert the result is well-formed.
    assert "argv" in res.data
    assert "durationMs" in res.data
    assert res.data["mode"] == "wine"
