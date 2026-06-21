"""Streaming-friendly VCD normalization and min/max previews."""

from __future__ import annotations

import json
import math
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class SignalSeries:
    name: str
    width: int
    times: list[float]
    values: list[float]


def parse_vcd(path: Path) -> dict[str, SignalSeries]:
    codes: dict[str, SignalSeries] = {}
    scopes: list[str] = []
    current_time = 0
    in_header = True
    with path.open(encoding="utf-8", errors="replace") as stream:
        for raw_line in stream:
            line = raw_line.strip()
            if not line:
                continue
            if in_header:
                parts = line.split()
                if line.startswith("$scope") and len(parts) >= 3:
                    scopes.append(parts[2])
                elif line.startswith("$upscope") and scopes:
                    scopes.pop()
                elif line.startswith("$var") and len(parts) >= 5:
                    width = int(parts[2])
                    code = parts[3]
                    name = ".".join([*scopes, parts[4]])
                    codes[code] = SignalSeries(name=name, width=width, times=[], values=[])
                elif line.startswith("$enddefinitions"):
                    in_header = False
                continue
            if line.startswith("#"):
                current_time = int(line[1:])
                continue
            if line[0] in "01xXzZ":
                value, code = line[0], line[1:]
            elif line[0] in "bB":
                parts = line[1:].split(maxsplit=1)
                if len(parts) != 2:
                    continue
                value, code = parts
            else:
                continue
            series = codes.get(code)
            if series is None:
                continue
            numeric = _vcd_number(value)
            if numeric is None:
                continue
            series.times.append(current_time)
            series.values.append(numeric)
    return {series.name: series for series in codes.values()}


def _vcd_number(value: str) -> float | None:
    if any(character in "xXzZ" for character in value):
        return None
    try:
        return float(int(value, 2))
    except ValueError:
        return None


def downsample_minmax(
    times: list[float], values: list[float], *, max_points: int
) -> list[list[float]]:
    if not times or max_points <= 0:
        return []
    if len(times) <= max_points:
        return [[float(time), value] for time, value in zip(times, values, strict=True)]
    bucket_size = math.ceil(len(times) / max(1, max_points // 2))
    points: list[list[float]] = []
    for start in range(0, len(times), bucket_size):
        end = min(len(times), start + bucket_size)
        indexes = range(start, end)
        low = min(indexes, key=values.__getitem__)
        high = max(indexes, key=values.__getitem__)
        for index in sorted({low, high}):
            points.append([float(times[index]), values[index]])
    return points[:max_points]


def write_vcd_bundle(
    vcd_path: Path,
    run_dir: Path,
    *,
    chunk_size: int = 4096,
    preview_points: int = 2000,
) -> str:
    return write_signal_bundle(
        parse_vcd(vcd_path),
        run_dir,
        chunk_size=chunk_size,
        preview_points=preview_points,
    )


def parse_ngspice_raw(path: Path) -> dict[str, SignalSeries]:
    payload = path.read_bytes()
    marker = b"Binary:\n"
    header_end = payload.find(marker)
    if header_end < 0:
        raise ValueError("ngspice raw file has no Binary section")
    header = payload[:header_end].decode("ascii", errors="replace")
    if re.search(r"^Flags:\s+.*complex", header, re.MULTILINE):
        raise ValueError("complex ngspice raw files are not supported")
    variables_match = re.search(r"^No\. Variables:\s+(\d+)", header, re.MULTILINE)
    points_match = re.search(r"^No\. Points:\s+(\d+)", header, re.MULTILINE)
    if variables_match is None or points_match is None:
        raise ValueError("ngspice raw header is incomplete")
    variable_count = int(variables_match.group(1))
    point_count = int(points_match.group(1))
    variables = re.findall(r"^\s*\d+\s+(\S+)\s+(\S+)\s*$", header, re.MULTILINE)
    if len(variables) != variable_count:
        raise ValueError("ngspice raw variable table does not match header")
    binary = payload[header_end + len(marker) :]
    expected_bytes = variable_count * point_count * 8
    if len(binary) < expected_bytes:
        raise ValueError("ngspice raw binary payload is truncated")
    values = struct.unpack_from(f"<{variable_count * point_count}d", binary)
    axis = [values[index * variable_count] for index in range(point_count)]
    signals: dict[str, SignalSeries] = {}
    for variable_index, (name, _unit) in enumerate(variables[1:], start=1):
        samples = [values[point * variable_count + variable_index] for point in range(point_count)]
        signals[name] = SignalSeries(name=name, width=1, times=list(axis), values=samples)
    return signals


def write_ngspice_bundle(
    raw_path: Path,
    run_dir: Path,
    *,
    chunk_size: int = 4096,
    preview_points: int = 2000,
) -> str:
    return write_signal_bundle(
        parse_ngspice_raw(raw_path),
        run_dir,
        chunk_size=chunk_size,
        preview_points=preview_points,
    )


def write_signal_bundle(
    signals: dict[str, SignalSeries],
    run_dir: Path,
    *,
    chunk_size: int,
    preview_points: int,
) -> str:
    waveform_dir = run_dir / "waveform"
    waveform_dir.mkdir(parents=True, exist_ok=True)
    index_signals: list[dict[str, Any]] = []
    for signal_index, series in enumerate(signals.values()):
        stem = f"signal_{signal_index}"
        preview_path = waveform_dir / f"{stem}.preview.json"
        _write_json(
            preview_path,
            {
                "name": series.name,
                "points": downsample_minmax(series.times, series.values, max_points=preview_points),
            },
        )
        chunks: list[str] = []
        for chunk_index, start in enumerate(range(0, len(series.times), chunk_size)):
            chunk_path = waveform_dir / f"{stem}.{chunk_index}.json"
            _write_json(
                chunk_path,
                {
                    "start": start,
                    "times": series.times[start : start + chunk_size],
                    "values": series.values[start : start + chunk_size],
                },
            )
            chunks.append(str(chunk_path.relative_to(run_dir)))
        index_signals.append(
            {
                "chunks": chunks,
                "max": max(series.values) if series.values else 0.0,
                "min": min(series.values) if series.values else 0.0,
                "name": series.name,
                "preview": str(preview_path.relative_to(run_dir)),
                "sampleCount": len(series.values),
                "width": series.width,
            }
        )
    index_path = waveform_dir / "index.json"
    _write_json(index_path, {"schemaVersion": "1.0", "signals": index_signals})
    return str(index_path.relative_to(run_dir))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        encoding="utf-8",
    )


__all__ = [
    "SignalSeries",
    "downsample_minmax",
    "parse_ngspice_raw",
    "parse_vcd",
    "write_ngspice_bundle",
    "write_signal_bundle",
    "write_vcd_bundle",
]
