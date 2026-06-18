"""Diagnostics for the local LTspice / Wine environment.

Every check in this module is a pure function that takes the resources it
needs as arguments and returns a ``CheckResult``. The ``run_doctor`` entry
point composes them and produces the JSON output documented in
``docs/SPEC.md``.

The smoke simulation (the only check that touches the configured LTspice
executable) is gated behind the ``--simulate`` flag and is implemented to
never raise: any exception is captured and returned as a structured
``fail`` result.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

from . import __version__
from .config import Config

MIN_PYTHON = (3, 11)

WELL_KNOWN_WINE_PATHS: tuple[str, ...] = (
    "/opt/wine-stable/bin/wine",
    "/usr/bin/wine",
    "/usr/local/bin/wine",
    "/snap/bin/wine",
)

SMOKE_NETLIST = (
    "* ltagent doctor smoke simulation\n"
    "V1 in 0 1\n"
    "R1 in out 1k\n"
    "R2 out 0 1k\n"
    ".op\n"
    ".meas op VOUT FIND V(out)\n"
    ".end\n"
)


@dataclass(frozen=True)
class CheckResult:
    """A single diagnostic check.

    ``status`` is one of ``ok``, ``warn``, ``fail``, ``skip``.
    ``code`` is a stable, machine-readable identifier (e.g. ``PYTHON_OK``).
    ``detail`` is a short human-readable string.
    ``data`` is an optional dict of machine-readable details.
    """

    name: str
    status: str
    code: str
    detail: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ok(name: str, code: str, detail: str, **data: Any) -> CheckResult:
    return CheckResult(name=name, status="ok", code=code, detail=detail, data=data)


def _warn(name: str, code: str, detail: str, **data: Any) -> CheckResult:
    return CheckResult(name=name, status="warn", code=code, detail=detail, data=data)


def _fail(name: str, code: str, detail: str, **data: Any) -> CheckResult:
    return CheckResult(name=name, status="fail", code=code, detail=detail, data=data)


def _skip(name: str, code: str, detail: str, **data: Any) -> CheckResult:
    return CheckResult(name=name, status="skip", code=code, detail=detail, data=data)


# --- individual checks -----------------------------------------------------


def check_python() -> CheckResult:
    v = sys.version_info
    major, minor, micro = v[0], v[1], v[2]
    version_str = f"{major}.{minor}.{micro}"
    if (major, minor) >= MIN_PYTHON:
        return _ok(
            "python_version",
            "PYTHON_OK",
            f"Python {version_str} meets >= {'.'.join(map(str, MIN_PYTHON))}",
            version=version_str,
        )
    return _fail(
        "python_version",
        "PYTHON_TOO_OLD",
        f"Python {version_str} is older than required {'.'.join(map(str, MIN_PYTHON))}",
        version=version_str,
        required=".".join(map(str, MIN_PYTHON)),
    )


def check_package_version() -> CheckResult:
    try:
        v = metadata.version("ltspice-ai-agent")
    except metadata.PackageNotFoundError:
        return _warn(
            "package_version",
            "PACKAGE_NOT_INSTALLED",
            "ltspice-ai-agent is not importable as an installed package",
            resolvedVersion=__version__,
        )
    if v != __version__:
        return _warn(
            "package_version",
            "PACKAGE_VERSION_MISMATCH",
            f"installed {v} != __init__ {__version__}",
            installedVersion=v,
            initVersion=__version__,
        )
    return _ok(
        "package_version",
        "PACKAGE_OK",
        f"ltspice-ai-agent {v}",
        version=v,
    )


def check_config(config: Config) -> CheckResult:
    if config.source_path is None:
        return _warn(
            "config",
            "CONFIG_USING_DEFAULTS",
            "no config file found; using built-in defaults",
            searchPaths=_config_search_report(),
        )
    return _ok(
        "config",
        "CONFIG_OK",
        f"loaded {config.source_path}",
        path=str(config.source_path),
    )


def check_workspace_writable(projects_dir: Path) -> CheckResult:
    try:
        projects_dir.mkdir(parents=True, exist_ok=True)
        probe = projects_dir / ".ltagent_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return _fail(
            "workspace_writable",
            "WORKSPACE_NOT_WRITABLE",
            f"cannot write to {projects_dir}: {exc}",
            path=str(projects_dir),
        )
    return _ok(
        "workspace_writable",
        "WORKSPACE_OK",
        f"{projects_dir} is writable",
        path=str(projects_dir),
    )


def check_executable(path: str | None) -> CheckResult:
    if not path:
        return _fail(
            "lt_spice_executable",
            "LTSPICE_EXECUTABLE_NOT_SET",
            "ltspice.executable is empty; set it in config.toml",
        )
    p = Path(path).expanduser()
    if not p.is_file():
        return _fail(
            "lt_spice_executable",
            "LTSPICE_EXECUTABLE_MISSING",
            f"{p} does not exist or is not a file",
            path=str(p),
        )
    data: dict[str, Any] = {"path": str(p), "sizeBytes": p.stat().st_size}
    if not os.access(p, os.X_OK):
        # On Linux/Wine, a `.exe` under Wine is a regular file (not +x in the
        # POSIX sense). Don't fail on this; warn instead.
        return _warn(
            "lt_spice_executable",
            "LTSPICE_EXECUTABLE_NOT_POSIX_EXEC",
            f"{p} is not marked executable for the current user (expected for Wine .exe files)",
            **data,
        )
    return _ok(
        "lt_spice_executable",
        "LTSPICE_EXECUTABLE_OK",
        f"{p} exists",
        **data,
    )


def check_wine(configured: str | None) -> CheckResult:
    """Detect a usable ``wine`` binary.

    Search order:
    1. ``configured`` if non-empty and executable
    2. ``shutil.which("wine")`` (PATH lookup)
    3. The well-known fallbacks in :data:`WELL_KNOWN_WINE_PATHS`
    """
    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    on_path = shutil.which("wine")
    if on_path:
        candidates.append(on_path)
    candidates.extend(WELL_KNOWN_WINE_PATHS)

    for c in candidates:
        cp = Path(c)
        if not cp.is_file():
            continue
        try:
            completed = subprocess.run(
                [c, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return _warn(
                "wine",
                "WINE_LAUNCH_ERROR",
                f"failed to run {c}: {exc}",
                candidate=c,
            )
        version = (completed.stdout or completed.stderr or "").strip().splitlines()
        version_str = version[0] if version else "(no output)"
        return _ok(
            "wine",
            "WINE_OK",
            f"wine: {version_str}",
            path=c,
            version=version_str,
        )

    return _fail(
        "wine",
        "WINE_NOT_FOUND",
        "no wine binary found in PATH or well-known locations",
        searched=[c for c in candidates if c],
    )


def check_wine_prefix(home: Path | None = None) -> CheckResult:
    if home is None:
        home = Path.home()
    prefix = home / ".wine"
    drive_c = prefix / "drive_c"
    if drive_c.is_dir():
        return _ok(
            "wine_prefix",
            "WINE_PREFIX_OK",
            f"wine prefix at {prefix}",
            path=str(prefix),
        )
    return _warn(
        "wine_prefix",
        "WINE_PREFIX_MISSING",
        f"no wine prefix at {prefix}; LTspice will not be reachable under wine mode",
        path=str(prefix),
    )


def check_temp_writable() -> CheckResult:
    try:
        with tempfile.NamedTemporaryFile(
            prefix="ltagent_doctor_",
            suffix=".cir",
            delete=False,
        ) as fh:
            fh.write(SMOKE_NETLIST.encode("utf-8"))
            tmp_path = Path(fh.name)
        try:
            data = tmp_path.read_text(encoding="utf-8")
        finally:
            tmp_path.unlink(missing_ok=True)
    except OSError as exc:
        return _fail(
            "temp_writable",
            "TEMP_NOT_WRITABLE",
            f"cannot write/read temp .cir: {exc}",
        )
    if "V1" not in data:
        return _fail(
            "temp_writable",
            "TEMP_ROUNDTRIP_BROKEN",
            "temp .cir was written but content was not round-trippable",
        )
    return _ok(
        "temp_writable",
        "TEMP_OK",
        "temp .cir round-trip succeeded",
    )


def check_log_parser_loaded() -> CheckResult:
    """The log parser lands in Phase 4. For now, report its stub presence."""
    try:
        from . import log_parser  # noqa: F401
    except ImportError as exc:
        return _skip(
            "log_parser",
            "LOG_PARSER_NOT_IMPLEMENTED",
            f"log parser module not yet available: {exc}",
        )
    return _ok(
        "log_parser",
        "LOG_PARSER_PRESENT",
        "log parser module is importable",
    )


def check_lt_spice_smoke(
    config: Config,
    *,
    run_subprocess: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    clock: Callable[[], float] = lambda: 0.0,
) -> CheckResult:
    """Attempt a tiny ``.op`` simulation. Never raises."""
    if not config.ltspice.executable:
        return _skip(
            "lt_spice_smoke_simulate",
            "LTSPICE_EXECUTABLE_NOT_SET",
            "skipped: ltspice.executable is empty",
        )
    exe = Path(config.ltspice.executable).expanduser()
    if not exe.is_file():
        return _skip(
            "lt_spice_smoke_simulate",
            "LTSPICE_EXECUTABLE_MISSING",
            f"skipped: {exe} does not exist",
            path=str(exe),
        )

    with tempfile.TemporaryDirectory(prefix="ltagent_smoke_") as tmp:
        workdir = Path(tmp)
        cir_path = workdir / "smoke.cir"
        cir_path.write_text(SMOKE_NETLIST, encoding="utf-8")

        if config.ltspice.mode == "wine":
            wine = _resolve_wine_for_run(config.ltspice.wine_command)
            if not wine:
                return _skip(
                    "lt_spice_smoke_simulate",
                    "WINE_NOT_FOUND",
                    "skipped: no wine binary resolved",
                )
            argv: Sequence[str] = [wine, str(exe), "-b", str(cir_path)]
        elif config.ltspice.mode == "native":
            argv = [str(exe), "-b", str(cir_path)]
        else:
            return _fail(
                "lt_spice_smoke_simulate",
                "LTSPICE_MODE_INVALID",
                f"unknown ltspice.mode: {config.ltspice.mode!r}",
            )

        timeout = max(1, int(config.runner.timeout_seconds))
        try:
            completed = run_subprocess(
                list(argv),
                capture_output=True,
                text=True,
                cwd=str(workdir),
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return _fail(
                "lt_spice_smoke_simulate",
                "LTSPICE_TIMEOUT",
                f"timed out after {timeout}s with no .log produced",
                timeoutSeconds=timeout,
                argv=list(argv),
                stdoutTail=(exc.stdout or "")[-400:] if isinstance(exc.stdout, str) else None,
            )
        except FileNotFoundError as exc:
            return _fail(
                "lt_spice_smoke_simulate",
                "LTSPICE_LAUNCH_ERROR",
                f"failed to launch: {exc}",
                argv=list(argv),
            )
        except OSError as exc:
            return _fail(
                "lt_spice_smoke_simulate",
                "LTSPICE_LAUNCH_ERROR",
                f"OS error launching: {exc}",
                argv=list(argv),
            )

        log_path = workdir / "smoke.log"
        if not log_path.is_file():
            return _fail(
                "lt_spice_smoke_simulate",
                "LTSPICE_NO_LOG",
                "process exited but no .log was produced",
                exitCode=completed.returncode,
                stdoutTail=completed.stdout[-400:],
                stderrTail=completed.stderr[-400:],
                argv=list(argv),
            )

        return _ok(
            "lt_spice_smoke_simulate",
            "LTSPICE_OK",
            f"smoke .op produced {log_path.name}",
            exitCode=completed.returncode,
            argv=list(argv),
            logBytes=log_path.stat().st_size,
        )


# --- helpers ---------------------------------------------------------------


def _resolve_wine_for_run(configured: str | None) -> str | None:
    if configured and Path(configured).is_file():
        return configured
    on_path = shutil.which("wine")
    if on_path:
        return on_path
    for p in WELL_KNOWN_WINE_PATHS:
        if Path(p).is_file():
            return p
    return None


def _config_search_report() -> list[str]:
    from .config import search_paths_report

    return search_paths_report()


# --- public entry point ---------------------------------------------------


def run_doctor(
    config: Config,
    *,
    simulate: bool = False,
    run_subprocess: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    home: Path | None = None,
    projects_dir: Path | None = None,
) -> list[CheckResult]:
    """Run all checks and return their results.

    All checks are safe to run on a host without LTspice or Wine.
    """
    if home is None:
        home = Path.home()
    if projects_dir is None:
        projects_dir = Path(config.workspace.projects_dir)
    checks: list[CheckResult] = [
        check_python(),
        check_package_version(),
        check_config(config),
        check_workspace_writable(projects_dir),
        check_executable(config.ltspice.executable),
        check_wine(config.ltspice.wine_command),
        check_wine_prefix(home=home),
        check_temp_writable(),
        check_log_parser_loaded(),
    ]
    if simulate:
        checks.append(
            check_lt_spice_smoke(
                config,
                run_subprocess=run_subprocess,
            )
        )
    return checks


def aggregate_status(checks: Sequence[CheckResult]) -> str:
    """Reduce a list of check results to a single top-level status."""
    statuses = {c.status for c in checks}
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    if statuses == {"skip"}:
        return "skip"
    return "ok"


def to_json_payload(
    command: str,
    message: str,
    checks: Sequence[CheckResult],
    *,
    data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap checks into the JSON output contract from ``docs/SPEC.md``."""
    warnings = [
        {"code": c.code, "detail": c.detail, "data": c.data}
        for c in checks
        if c.status == "warn"
    ]
    errors = [
        {"code": c.code, "detail": c.detail, "data": c.data}
        for c in checks
        if c.status == "fail"
    ]
    payload: dict[str, Any] = {
        "success": not errors,
        "command": command,
        "message": message,
        "data": {
            "status": aggregate_status(checks),
            "checks": [c.to_dict() for c in checks],
            **(dict(data) if data else {}),
        },
        "warnings": warnings,
        "errors": errors,
    }
    return payload
