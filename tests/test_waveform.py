from __future__ import annotations

import json
import struct
from pathlib import Path

from ltagent.waveform import (
    downsample_minmax,
    parse_ngspice_raw,
    parse_vcd,
    write_vcd_bundle,
)

VCD = """$timescale 1ns $end
$scope module tb $end
$var wire 1 ! clk $end
$var wire 4 \" count [3:0] $end
$upscope $end
$enddefinitions $end
#0
0!
b0000 \"
#5
1!
b0001 \"
#10
0!
b0010 \"
"""


def test_parse_vcd_returns_numeric_signal_series(tmp_path: Path) -> None:
    path = tmp_path / "waveform.vcd"
    path.write_text(VCD, encoding="utf-8")

    parsed = parse_vcd(path)

    assert parsed["tb.clk"].times == [0, 5, 10]
    assert parsed["tb.clk"].values == [0.0, 1.0, 0.0]
    assert parsed["tb.count"].values == [0.0, 1.0, 2.0]


def test_downsample_minmax_bounds_preview_size_and_keeps_extrema() -> None:
    times = list(range(1000))
    values = [float(index % 100) for index in times]

    preview = downsample_minmax(times, values, max_points=40)

    assert len(preview) <= 40
    assert min(point[1] for point in preview) == 0.0
    assert max(point[1] for point in preview) == 99.0


def test_write_vcd_bundle_creates_small_index_preview_and_chunks(tmp_path: Path) -> None:
    vcd = tmp_path / "waveform.vcd"
    vcd.write_text(VCD, encoding="utf-8")

    relative_index = write_vcd_bundle(vcd, tmp_path, chunk_size=2)
    index = json.loads((tmp_path / relative_index).read_text(encoding="utf-8"))

    assert index["schemaVersion"] == "1.0"
    assert [item["name"] for item in index["signals"]] == ["tb.clk", "tb.count"]
    assert len(index["signals"][0]["chunks"]) == 2
    assert (tmp_path / index["signals"][0]["preview"]).is_file()


def test_parse_ngspice_binary_raw(tmp_path: Path) -> None:
    path = tmp_path / "waveform.raw"
    header = (
        b"Title: test\nPlotname: Transient Analysis\nFlags: real\n"
        b"No. Variables: 2\nNo. Points: 3\nVariables:\n"
        b"\t0\ttime\ttime\n\t1\tv(out)\tvoltage\nBinary:\n"
    )
    path.write_bytes(header + struct.pack("<6d", 0.0, 0.0, 1.0, 2.5, 2.0, 5.0))

    parsed = parse_ngspice_raw(path)

    assert parsed["v(out)"].times == [0.0, 1.0, 2.0]
    assert parsed["v(out)"].values == [0.0, 2.5, 5.0]
