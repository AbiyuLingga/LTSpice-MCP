from __future__ import annotations

import sys
import threading
import time

from ltagent.tool_process import run_tool_process


def test_tool_process_captures_output() -> None:
    result = run_tool_process(
        [sys.executable, "-c", "print('ready')"],
        timeout_seconds=2,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "ready"
    assert result.cancelled is False
    assert result.timed_out is False


def test_tool_process_cancellation_stops_process_group() -> None:
    cancel = threading.Event()
    timer = threading.Timer(0.1, cancel.set)
    timer.start()
    started = time.monotonic()
    try:
        result = run_tool_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout_seconds=10,
            cancel_event=cancel,
        )
    finally:
        timer.cancel()

    assert result.cancelled is True
    assert time.monotonic() - started < 2


def test_tool_process_timeout_stops_process_group() -> None:
    result = run_tool_process(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout_seconds=0.1,
    )

    assert result.timed_out is True
