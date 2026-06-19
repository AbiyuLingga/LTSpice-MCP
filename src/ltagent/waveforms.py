"""Safe waveform normalisation for VCD and numeric simulator traces."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Final

MAX_VCD_EVENTS: Final[int] = 1_000_000
MAX_VCD_SIGNALS: Final[int] = 10_000
ERR_VCD_IDENTIFIER_UNKNOWN: Final[str] = "WAVEFORM_VCD_IDENTIFIER_UNKNOWN"
ERR_VCD_INVALID: Final[str] = "WAVEFORM_VCD_INVALID"
ERR_VCD_LIMIT: Final[str] = "WAVEFORM_VCD_LIMIT"


class WaveformError(ValueError):
    """Structured parser rejection for untrusted waveform artefacts."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class DigitalTrace:
    """Value transitions for one declared VCD signal."""

    name: str
    identifier: str
    width: int
    times: tuple[int, ...]
    values: tuple[str, ...]


@dataclass(frozen=True)
class Waveform:
    """Parsed digital waveform preserving VCD declaration order."""

    timescale: str | None
    signals: tuple[DigitalTrace, ...]

    def signal(self, name: str) -> DigitalTrace:
        for trace in self.signals:
            if trace.name == name:
                return trace
        raise KeyError(name)


@dataclass(frozen=True)
class NumericTrace:
    """Bounded numeric samples suitable for chart rendering."""

    times: tuple[int, ...]
    values: tuple[float, ...]


def parse_vcd(
    text: str,
    *,
    max_events: int = MAX_VCD_EVENTS,
    max_signals: int = MAX_VCD_SIGNALS,
) -> Waveform:
    """Parse scalar/vector VCD changes without executing directives.

    The parser accepts the portable subset emitted by Icarus and Verilator:
    inline ``$timescale``, ``$var`` declarations, timestamps, scalar changes,
    and binary-vector changes. Unknown directives are ignored because they do
    not change the signal timeline; unknown value identifiers are rejected.
    """
    if max_events < 1 or max_signals < 1:
        raise ValueError("max_events and max_signals must be positive")

    declarations: dict[str, tuple[str, int]] = {}
    changes: dict[str, list[tuple[int, str]]] = {}
    timescale: str | None = None
    current_time = 0
    end_definitions = False
    event_count = 0

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("$timescale"):
            timescale = _parse_timescale(line, line_number)
            continue
        if line.startswith("$var"):
            identifier, name, width = _parse_variable(line, line_number)
            if identifier in declarations:
                raise WaveformError(
                    ERR_VCD_INVALID,
                    f"duplicate VCD identifier {identifier!r} on line {line_number}",
                )
            if len(declarations) >= max_signals:
                raise WaveformError(ERR_VCD_LIMIT, "VCD signal limit exceeded")
            declarations[identifier] = (name, width)
            changes[identifier] = []
            continue
        if line.startswith("$enddefinitions"):
            end_definitions = True
            continue
        if line.startswith("$"):
            continue
        if line.startswith("#"):
            current_time = _parse_timestamp(line, line_number)
            continue
        if not end_definitions:
            raise WaveformError(
                ERR_VCD_INVALID,
                f"value change before $enddefinitions on line {line_number}",
            )
        identifier, value = _parse_value_change(line, line_number)
        if identifier not in declarations:
            raise WaveformError(
                ERR_VCD_IDENTIFIER_UNKNOWN,
                f"VCD value change uses unknown identifier {identifier!r}",
            )
        prior = changes[identifier]
        if prior and prior[-1][1] == value:
            continue
        event_count += 1
        if event_count > max_events:
            raise WaveformError(ERR_VCD_LIMIT, "VCD value-change limit exceeded")
        prior.append((current_time, value))

    if not end_definitions:
        raise WaveformError(ERR_VCD_INVALID, "VCD is missing $enddefinitions")
    traces = tuple(
        DigitalTrace(
            name=declarations[identifier][0],
            identifier=identifier,
            width=declarations[identifier][1],
            times=tuple(time for time, _value in changes[identifier]),
            values=tuple(value for _time, value in changes[identifier]),
        )
        for identifier in declarations
    )
    return Waveform(timescale=timescale, signals=traces)


def downsample_numeric(
    times: Sequence[int], values: Sequence[float], *, max_points: int
) -> NumericTrace:
    """Return a min/max bucket view that preserves endpoints and extrema."""
    if len(times) != len(values):
        raise ValueError("times and values must have the same length")
    if max_points < 3:
        raise ValueError("max_points must be at least 3")
    checked_times = tuple(_validate_time(value) for value in times)
    checked_values = tuple(_validate_value(value) for value in values)
    if any(later < earlier for earlier, later in pairwise(checked_times)):
        raise ValueError("times must be non-decreasing")
    if len(checked_times) <= max_points:
        return NumericTrace(times=checked_times, values=checked_values)

    interior_count = len(checked_times) - 2
    bucket_count = max(1, (max_points - 2) // 2)
    selected = {0, len(checked_times) - 1}
    for bucket in range(bucket_count):
        start = 1 + (bucket * interior_count) // bucket_count
        end = 1 + ((bucket + 1) * interior_count) // bucket_count
        if start >= end:
            continue
        indexes = range(start, end)
        minimum = min(indexes, key=lambda index: checked_values[index])
        maximum = max(range(start, end), key=lambda index: checked_values[index])
        selected.add(minimum)
        selected.add(maximum)
    ordered = tuple(sorted(selected))
    return NumericTrace(
        times=tuple(checked_times[index] for index in ordered),
        values=tuple(checked_values[index] for index in ordered),
    )


def _parse_timescale(line: str, line_number: int) -> str:
    tokens = line.split()
    if len(tokens) < 3 or tokens[-1] != "$end":
        raise WaveformError(ERR_VCD_INVALID, f"invalid $timescale on line {line_number}")
    value = "".join(tokens[1:-1])
    if not value:
        raise WaveformError(ERR_VCD_INVALID, f"empty $timescale on line {line_number}")
    return value


def _parse_variable(line: str, line_number: int) -> tuple[str, str, int]:
    tokens = line.split()
    if len(tokens) < 6 or tokens[-1] != "$end":
        raise WaveformError(ERR_VCD_INVALID, f"invalid $var declaration on line {line_number}")
    try:
        width = int(tokens[2], 10)
    except ValueError as exc:
        raise WaveformError(ERR_VCD_INVALID, f"invalid VCD width on line {line_number}") from exc
    if width < 1:
        raise WaveformError(ERR_VCD_INVALID, f"VCD width must be positive on line {line_number}")
    return tokens[3], " ".join(tokens[4:-1]), width


def _parse_timestamp(line: str, line_number: int) -> int:
    try:
        value = int(line[1:], 10)
    except ValueError as exc:
        raise WaveformError(ERR_VCD_INVALID, f"invalid timestamp on line {line_number}") from exc
    if value < 0:
        raise WaveformError(ERR_VCD_INVALID, f"negative timestamp on line {line_number}")
    return value


def _parse_value_change(line: str, line_number: int) -> tuple[str, str]:
    prefix = line[0]
    if prefix in "01xXzZ":
        identifier = line[1:].strip()
        if not identifier:
            raise WaveformError(ERR_VCD_INVALID, f"missing identifier on line {line_number}")
        return identifier, prefix.lower()
    if prefix in "bB":
        pieces = line[1:].split(maxsplit=1)
        if len(pieces) != 2 or not pieces[0] or not pieces[1]:
            raise WaveformError(ERR_VCD_INVALID, f"invalid binary value on line {line_number}")
        if any(character not in "01xXzZ" for character in pieces[0]):
            raise WaveformError(ERR_VCD_INVALID, f"invalid binary bits on line {line_number}")
        return pieces[1], pieces[0].lower()
    raise WaveformError(ERR_VCD_INVALID, f"unsupported VCD value change on line {line_number}")


def _validate_time(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("times must contain non-negative integers")
    return value


def _validate_value(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("values must contain finite numbers")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError("values must contain finite numbers")
    return converted


__all__ = [
    "ERR_VCD_IDENTIFIER_UNKNOWN",
    "ERR_VCD_INVALID",
    "ERR_VCD_LIMIT",
    "MAX_VCD_EVENTS",
    "MAX_VCD_SIGNALS",
    "DigitalTrace",
    "NumericTrace",
    "Waveform",
    "WaveformError",
    "downsample_numeric",
    "parse_vcd",
]
