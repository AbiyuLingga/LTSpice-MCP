"""Waveform parsing and bounded visualisation data tests."""

from __future__ import annotations

import pytest

from ltagent.waveforms import (
    WaveformError,
    downsample_numeric,
    parse_vcd,
)

VCD = """$timescale 1ns $end
$scope module top $end
$var wire 1 ! clk $end
$var wire 8 \" led [7:0] $end
$upscope $end
$enddefinitions $end
#0
0!
b00000000 \"
#5
1!
b00000001 \"
#10
0!
"""


def test_parse_vcd_preserves_signal_names_values_and_transitions() -> None:
    waveform = parse_vcd(VCD)

    assert waveform.timescale == "1ns"
    assert waveform.signal("clk").times == (0, 5, 10)
    assert waveform.signal("clk").values == ("0", "1", "0")
    assert waveform.signal("led [7:0]").values[-1] == "00000001"


def test_parse_vcd_rejects_value_changes_for_undeclared_identifiers() -> None:
    with pytest.raises(WaveformError) as excinfo:
        parse_vcd("$enddefinitions $end\n#0\n1!\n")

    assert excinfo.value.code == "WAVEFORM_VCD_IDENTIFIER_UNKNOWN"


def test_minmax_downsample_preserves_endpoints_and_extrema() -> None:
    times = list(range(20))
    values = [0.0] * 20
    values[5] = 9.0
    values[14] = -7.0

    result = downsample_numeric(times, values, max_points=6)

    assert result.times[0] == 0
    assert result.times[-1] == 19
    assert 9.0 in result.values
    assert -7.0 in result.values
    assert len(result.times) <= 6


def test_minmax_downsample_rejects_mismatched_or_too_small_inputs() -> None:
    with pytest.raises(ValueError):
        downsample_numeric([0], [0.0, 1.0], max_points=4)
    with pytest.raises(ValueError):
        downsample_numeric([0, 1, 2], [0.0, 1.0, 2.0], max_points=2)
