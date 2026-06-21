"""Phase 3: LTspice batch runner with timeout, safe subprocess, and structured output.

The runner is the only code path in ``ltagent`` that actually launches
LTspice. It is intentionally small and self-contained so it can be
exercised by unit tests without a real LTspice install.

Responsibilities (per plan section 13):

* Execute **only** the configured LTspice executable. No shell, no glob.
* Support Windows native (``mode="native"``) and Linux/Wine
  (``mode="wine"``) invocations.
* Run inside a controlled working directory (typically the project's
  temp dir or the configured ``working_dir``).
* Capture stdout and stderr, enforce a timeout, and kill the child on
  timeout when possible.
* Detect ``.log`` and ``.raw`` output files and report their existence
  and sizes.
* Return a :class:`RunResult` that conforms to the JSON output contract
  in ``docs/SPEC.md``. **The runner never raises**; every failure path
  is encoded as ``success=False`` with a stable error code.

Hard rules (per AGENTS.md):

* ``subprocess.run`` is called with a list of args, never
  ``shell=True``.
* All paths are resolved and rejected if they would escape the
  configured workspace.
* Timeouts are enforced on every simulation.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, NoReturn, Protocol

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Well-known wine binary locations, searched in order when neither
#: ``ltspice.wine_command`` nor ``shutil.which("wine")`` resolves. Used
#: by :func:`resolve_wine` and by ``ltagent doctor``.
WELL_KNOWN_WINE_PATHS: tuple[str, ...] = (
    "/opt/wine-stable/bin/wine",
    "/usr/bin/wine",
    "/usr/local/bin/wine",
    "/snap/bin/wine",
)

#: Default LTspice timeout. Anything below 5s is treated as 5s; the
#: Wine startup cost on cold prefixes is real.
MIN_TIMEOUT_SECONDS = 5

#: Marker used by :func:`pytest.mark.integration` to gate tests that
#: require a real LTspice / Wine install.
INTEGRATION_MARKER = "integration"

#: Stable error codes returned in :class:`RunResult.errors`.
ERR_EXECUTABLE_NOT_SET = "LTSPICE_EXECUTABLE_NOT_SET"
ERR_EXECUTABLE_MISSING = "LTSPICE_EXECUTABLE_MISSING"
ERR_WINE_NOT_FOUND = "WINE_NOT_FOUND"
ERR_MODE_INVALID = "LTSPICE_MODE_INVALID"
ERR_CIR_MISSING = "LTSPICE_CIR_MISSING"
ERR_CIR_NOT_FILE = "LTSPICE_CIR_NOT_FILE"
ERR_LAUNCH = "LTSPICE_LAUNCH_ERROR"
ERR_TIMEOUT = "LTSPICE_TIMEOUT"
ERR_NO_LOG = "LTSPICE_NO_LOG"


# ---------------------------------------------------------------------------
# Subprocess protocol
# ---------------------------------------------------------------------------


class _SubprocessRunner(Protocol):
    """Minimal protocol for the subprocess layer.

    Defined as a ``Protocol`` so tests can inject a fake without
    subclassing. The default implementation is
    ``subprocess.run`` itself.
    """

    def __call__(  # protocol signature
        self,
        args: Sequence[str],
        *,
        cwd: str | None = None,
        capture_output: bool = True,
        text: bool = True,
        timeout: float | None = None,
        check: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]: ...


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunRequest:
    """Input contract for a single LTspice batch run.

    All paths are stored as :class:`Path` objects; they are not resolved
    until :func:`run_simulation` is called so callers can construct
    requests without touching the filesystem.
    """

    cir_path: Path
    workdir: Path
    timeout_seconds: int = 30
    mode: str = "wine"
    executable: str | None = None
    wine_command: str | None = None
    # Extra argv to pass to LTspice (e.g. ``-ascii``, ``-netlist``). Kept
    # as a tuple so the request is hashable and immutable.
    extra_args: tuple[str, ...] = field(default_factory=tuple)
    # Optional override for the expected ``.log`` file. Default: same
    # stem as ``cir_path`` with ``.log`` extension, located in
    # ``workdir``.
    expected_log_name: str | None = None
    # If True, enforce that the working directory is contained within
    # ``workdir`` (i.e. the .cir lives under it). Default True.
    require_cir_under_workdir: bool = True


@dataclass(frozen=True)
class RunResult:
    """Output contract for a single LTspice batch run.

    Matches the JSON output contract in ``docs/SPEC.md``:

    ::

        {
          "success": bool,
          "command": "run",
          "message": str,
          "data": {
            "logPath": str | None,
            "rawPath": str | None,
            "logBytes": int | None,
            "rawBytes": int | None,
            "exitCode": int | None,
            "durationMs": int | None,
            "timeoutSeconds": int,
            "argv": [str, ...],
            "mode": "wine" | "native",
            "workdir": str
          },
          "warnings": [{"code", "detail", "data"}, ...],
          "errors":   [{"code", "detail", "data"}, ...]
        }
    """

    success: bool
    command: str
    message: str
    data: dict[str, Any]
    warnings: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Wine resolution
# ---------------------------------------------------------------------------


def resolve_wine(configured: str | None) -> str | None:
    """Return the absolute path of a usable ``wine`` binary, or ``None``.

    Search order:

    1. ``configured`` if it is a non-empty path to an existing file.
    2. ``shutil.which("wine")`` (PATH lookup).
    3. :data:`WELL_KNOWN_WINE_PATHS`.
    """
    if configured:
        p = Path(configured).expanduser()
        if p.is_file():
            return str(p.resolve())
    on_path = shutil.which("wine")
    if on_path:
        return on_path
    for candidate in WELL_KNOWN_WINE_PATHS:
        p = Path(candidate)
        if p.is_file():
            return str(p.resolve())
    return None


# ---------------------------------------------------------------------------
# Argv construction
# ---------------------------------------------------------------------------


def _resolve_executable(executable: str | None) -> tuple[str | None, str | None]:
    """Return ``(resolved_path, error_code)`` for the configured executable.

    ``resolved_path`` is the absolute path string if the executable is
    configured and present; ``None`` otherwise. ``error_code`` is one
    of :data:`ERR_EXECUTABLE_NOT_SET` or :data:`ERR_EXECUTABLE_MISSING`
    when resolution fails, or ``None`` on success.
    """
    if not executable:
        return None, ERR_EXECUTABLE_NOT_SET
    p = Path(executable).expanduser()
    if not p.is_file():
        return None, ERR_EXECUTABLE_MISSING
    return str(p.resolve()), None


def _resolve_mode(mode: str) -> str | None:
    """Return the normalised mode or ``None`` if invalid."""
    if mode not in ("wine", "native"):
        return None
    return mode


def build_argv(
    request: RunRequest,
    *,
    wine_resolver: Callable[[str | None], str | None] = resolve_wine,
) -> list[str]:
    """Build the argv list to pass to ``subprocess.run``.

    Raises :class:`RunnerBuildError` for any error that should be
    surfaced to the caller **before** the subprocess is launched. The
    function is pure (no filesystem side effects beyond
    ``Path.resolve()`` on the configured executable) and can be unit
    tested directly.
    """
    normalised = _resolve_mode(request.mode)
    if normalised is None:
        raise RunnerBuildError(
            code=ERR_MODE_INVALID,
            detail=f"unknown ltspice mode: {request.mode!r}; expected 'wine' or 'native'",
        )
    exe, exe_err = _resolve_executable(request.executable)
    if exe_err is not None:
        if exe_err == ERR_EXECUTABLE_NOT_SET:
            raise RunnerBuildError(
                code=ERR_EXECUTABLE_NOT_SET,
                detail="ltspice.executable is empty",
            )
        raise RunnerBuildError(
            code=ERR_EXECUTABLE_MISSING,
            detail=f"ltspice executable not found: {request.executable}",
            data={"executable": str(request.executable)},
        )

    # At this point ``exe`` is guaranteed non-None: the check above only
    # returns on a non-error result.
    assert exe is not None
    # -b is batch mode; we always pass it. Extra args come after.
    # LTspice expects the .cir path as the last positional argument.
    base: list[str] = [exe, "-b", *request.extra_args, str(request.cir_path)]
    if normalised == "wine":
        wine = wine_resolver(request.wine_command)
        if not wine:
            raise RunnerBuildError(
                code=ERR_WINE_NOT_FOUND,
                detail=(
                    "wine binary not found; set ltspice.wine_command in config or install wine"
                ),
                data={"searched": _wine_search_trace(request.wine_command)},
            )
        return [wine, *base]
    return base


def _wine_search_trace(configured: str | None) -> list[str]:
    trace: list[str] = []
    if configured:
        trace.append(f"configured:{configured}")
    on_path = shutil.which("wine")
    if on_path:
        trace.append(f"path:{on_path}")
    trace.extend(f"wellknown:{p}" for p in WELL_KNOWN_WINE_PATHS)
    return trace


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


class RunnerBuildError(ValueError):
    """Raised by :func:`build_argv` for synchronous, pre-flight failures.

    The :func:`run_simulation` entry point catches this and converts it
    into a structured :class:`RunResult`. It is exposed so unit tests
    can assert on specific pre-flight error codes.
    """

    def __init__(self, code: str, detail: str, data: Mapping[str, Any] | None = None):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.data: dict[str, Any] = dict(data) if data else {}


def run_simulation(
    request: RunRequest,
    *,
    run_subprocess: _SubprocessRunner = subprocess.run,
    clock: Callable[[], float] = time.monotonic,
    wine_resolver: Callable[[str | None], str | None] = resolve_wine,
) -> RunResult:
    """Execute the LTspice batch run described by ``request``.

    Never raises. All error paths (missing executable, missing wine,
    subprocess failure, timeout, missing ``.log``) are encoded as a
    :class:`RunResult` with ``success=False`` and a stable error code.

    The function does not create or remove the working directory; the
    caller owns its lifecycle. The runner only writes inside
    ``request.workdir`` via the subprocess.

    ``wine_resolver`` is injected for tests; production code uses
    :func:`resolve_wine`.
    """
    # ---- validate paths -------------------------------------------------
    cir = request.cir_path
    workdir = request.workdir
    if request.require_cir_under_workdir:
        try:
            cir_resolved = cir.resolve()
            workdir_resolved = workdir.resolve()
        except OSError as exc:
            return _result(
                success=False,
                message="Failed to resolve paths",
                errors=[_errdict(ERR_LAUNCH, f"path resolve error: {exc}")],
                data={"mode": request.mode, "workdir": str(workdir)},
            )
        try:
            cir_resolved.relative_to(workdir_resolved)
        except ValueError:
            return _result(
                success=False,
                message="Refusing to run: .cir is outside the working directory",
                errors=[
                    _errdict(
                        ERR_LAUNCH,
                        f"{cir_resolved} is not under {workdir_resolved}",
                        {
                            "cirPath": str(cir_resolved),
                            "workdir": str(workdir_resolved),
                        },
                    )
                ],
                data={"mode": request.mode, "workdir": str(workdir_resolved)},
            )

    if not cir.exists():
        return _result(
            success=False,
            message="Circuit netlist not found",
            errors=[_errdict(ERR_CIR_MISSING, f"{cir} does not exist", {"cirPath": str(cir)})],
            data={"mode": request.mode, "workdir": str(workdir)},
        )
    if not cir.is_file():
        return _result(
            success=False,
            message="Circuit netlist is not a file",
            errors=[_errdict(ERR_CIR_NOT_FILE, f"{cir} is not a file", {"cirPath": str(cir)})],
            data={"mode": request.mode, "workdir": str(workdir)},
        )

    # ---- build argv -----------------------------------------------------
    try:
        argv = build_argv(request, wine_resolver=wine_resolver)
    except RunnerBuildError as exc:
        return _result(
            success=False,
            message=exc.detail,
            errors=[_errdict(exc.code, exc.detail, exc.data)],
            data={"mode": request.mode, "workdir": str(workdir)},
        )

    # ---- enforce timeout floor -----------------------------------------
    timeout = max(MIN_TIMEOUT_SECONDS, int(request.timeout_seconds))

    # ---- execute --------------------------------------------------------
    start = clock()
    try:
        completed = run_subprocess(
            list(argv),
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((clock() - start) * 1000)
        stdout_tail = _safe_tail(exc.stdout)
        stderr_tail = _safe_tail(exc.stderr)
        return _result(
            success=False,
            message=f"LTspice run timed out after {timeout}s",
            errors=[
                _errdict(
                    ERR_TIMEOUT,
                    f"timed out after {timeout}s with no .log produced",
                    {
                        "timeoutSeconds": timeout,
                        "argv": list(argv),
                        "stdoutTail": stdout_tail,
                        "stderrTail": stderr_tail,
                    },
                )
            ],
            data={
                "mode": request.mode,
                "workdir": str(workdir),
                "durationMs": duration_ms,
                "timeoutSeconds": timeout,
                "argv": list(argv),
            },
        )
    except FileNotFoundError as exc:
        return _result(
            success=False,
            message="Failed to launch LTspice",
            errors=[
                _errdict(
                    ERR_LAUNCH,
                    f"failed to launch: {exc}",
                    {"argv": list(argv)},
                )
            ],
            data={"mode": request.mode, "workdir": str(workdir), "argv": list(argv)},
        )
    except OSError as exc:
        return _result(
            success=False,
            message="OS error launching LTspice",
            errors=[_errdict(ERR_LAUNCH, f"OS error launching: {exc}", {"argv": list(argv)})],
            data={"mode": request.mode, "workdir": str(workdir), "argv": list(argv)},
        )
    duration_ms = int((clock() - start) * 1000)

    # ---- detect output files --------------------------------------------
    log_name = request.expected_log_name or (cir.stem + ".log")
    log_path = workdir / log_name
    raw_path = workdir / (cir.stem + ".raw")

    log_exists = log_path.is_file()
    raw_exists = raw_path.is_file()

    data: dict[str, Any] = {
        "mode": request.mode,
        "workdir": str(workdir),
        "argv": list(argv),
        "exitCode": completed.returncode,
        "durationMs": duration_ms,
        "timeoutSeconds": timeout,
        "logPath": str(log_path) if log_exists else None,
        "rawPath": str(raw_path) if raw_exists else None,
        "logBytes": log_path.stat().st_size if log_exists else None,
        "rawBytes": raw_path.stat().st_size if raw_exists else None,
    }
    warnings: list[dict[str, Any]] = []

    if not log_exists:
        return _result(
            success=False,
            message="LTspice exited but no .log was produced",
            errors=[
                _errdict(
                    ERR_NO_LOG,
                    "process exited but no .log was produced",
                    {
                        "exitCode": completed.returncode,
                        "stdoutTail": _safe_tail(completed.stdout),
                        "stderrTail": _safe_tail(completed.stderr),
                        "argv": list(argv),
                    },
                )
            ],
            data=data,
            warnings=warnings,
        )

    if completed.returncode != 0:
        warnings.append(
            {
                "code": "LTSPICE_NONZERO_EXIT",
                "detail": f"LTspice exited with code {completed.returncode}",
                "data": {"exitCode": completed.returncode, "argv": list(argv)},
            }
        )

    return _result(
        success=True,
        message=f"LTspice run completed in {duration_ms}ms",
        data=data,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _result(
    *,
    success: bool,
    message: str,
    data: Mapping[str, Any],
    errors: Sequence[Mapping[str, Any]] = (),
    warnings: Sequence[Mapping[str, Any]] = (),
) -> RunResult:
    return RunResult(
        success=success,
        command="run",
        message=message,
        data=dict(data),
        errors=[dict(e) for e in errors],
        warnings=[dict(w) for w in warnings],
    )


def _errdict(code: str, detail: str, data: Mapping[str, Any] | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"code": code, "detail": detail, "data": dict(data) if data else {}}
    return d


def _safe_tail(value: Any, *, limit: int = 400) -> str | None:
    """Return the trailing ``limit`` characters of a captured stream.

    Subprocess exceptions may carry ``stdout``/``stderr`` as bytes or
    strings (or ``None``). This helper normalises all of those into a
    short ``str`` suitable for structured error payloads.
    """
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="replace")
        except Exception:  # last-ditch decode guard
            value = value.decode("latin-1", errors="replace")
    text = str(value)
    if len(text) > limit:
        return text[-limit:]
    return text


# ---------------------------------------------------------------------------
# CLI integration helper
# ---------------------------------------------------------------------------


def run_from_config(
    cir_path: Path,
    *,
    workdir: Path,
    timeout_seconds: int,
    mode: str,
    executable: str | None,
    wine_command: str | None,
    extra_args: Sequence[str] = (),
) -> RunResult:
    """Convenience helper used by the ``ltagent run`` CLI subcommand.

    Translates the flat arguments into a :class:`RunRequest` and
    delegates to :func:`run_simulation`. Kept here so CLI code stays
    free of ``subprocess`` / argv construction logic.
    """
    request = RunRequest(
        cir_path=cir_path,
        workdir=workdir,
        timeout_seconds=timeout_seconds,
        mode=mode,
        executable=executable,
        wine_command=wine_command,
        extra_args=tuple(extra_args),
    )
    return run_simulation(request)


# ---------------------------------------------------------------------------
# Manual debug entry point
# ---------------------------------------------------------------------------


def _cli(argv: Sequence[str]) -> NoReturn:  # pragma: no cover - manual debug
    """Run a single simulation from the command line (no JSON contract).

    Intended for manual debugging only. The real CLI is in
    ``ltagent.cli``. Exits with code 0 on success, 1 on failure.
    """
    if len(argv) < 1:
        print("usage: python -m ltagent.runner <cir_path> [workdir] [timeout]", file=sys.stderr)
        sys.exit(2)
    cir = Path(argv[0]).expanduser()
    workdir = Path(argv[1]).expanduser() if len(argv) > 1 else cir.parent
    timeout = int(argv[2]) if len(argv) > 2 else 30
    request = RunRequest(
        cir_path=cir,
        workdir=workdir,
        timeout_seconds=timeout,
        mode="wine",
    )
    res = run_simulation(request)
    print(res.to_dict())
    sys.exit(0 if res.success else 1)


if __name__ == "__main__":  # pragma: no cover
    _cli(sys.argv[1:])
