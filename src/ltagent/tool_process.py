"""Bounded process-group execution for allowlisted EDA adapters."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from threading import Event


@dataclass(frozen=True)
class ToolProcessResult:
    returncode: int
    stdout: str
    stderr: str
    cancelled: bool = False
    timed_out: bool = False


def run_tool_process(
    argv: list[str],
    *,
    timeout_seconds: float,
    cancel_event: Event | None = None,
    cwd: Path | None = None,
) -> ToolProcessResult:
    """Run one internal argv and terminate its process group when interrupted."""
    if not argv:
        raise ValueError("tool argv cannot be empty")
    process = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        cwd=cwd,
    )
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            stdout, stderr = process.communicate(timeout=0.05)
            return ToolProcessResult(process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            cancelled = cancel_event is not None and cancel_event.is_set()
            timed_out = time.monotonic() >= deadline
            if not cancelled and not timed_out:
                continue
            _terminate_process_group(process)
            stdout, stderr = process.communicate()
            return ToolProcessResult(
                process.returncode,
                stdout,
                stderr,
                cancelled=cancelled,
                timed_out=timed_out,
            )


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=0.5)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


__all__ = ["ToolProcessResult", "run_tool_process"]
