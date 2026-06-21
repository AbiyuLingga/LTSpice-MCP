"""Unit tests for ``ltagent.log_parser`` (Phase 4).

Every fixture is a real-world-shape LTspice log fragment. The tests
exercise:

* ``.meas op FIND`` parsing (voltage divider and RC low-pass).
* ``.meas tran MAX/MIN/AVG/PP/RMS`` parsing with ``FROM x TO y``.
* ``.meas FIND ... AT <time>`` parsing.
* ``.op`` variable lines (e.g. ``v(out)=0.5``).
* Error pattern detection: Fatal, Singular, Timestep, Model not found,
  Subcircuit, Parse error, Generic ``ERROR:``.
* Warning detection (``Warning: ...``).
* ``simulation_finished`` flag (driven by the ``Elapsed time`` trailer).
* The legacy ``parse_meas_lines`` shim.
* End-to-end run via :func:`parse_log` (file path).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ltagent import log_parser
from ltagent.log_parser import (
    LOG_ERR_ERROR,
    LOG_ERR_FATAL,
    LOG_ERR_MODEL,
    LOG_ERR_PARSE,
    LOG_ERR_SINGULAR,
    LOG_ERR_SUBCKT,
    LOG_ERR_TERMINATION,
    LOG_ERR_TIMESTEP,
    LOG_ERR_WARNING,
    LogFinding,
    ParseReport,
    findings_to_errors,
    merge_measurements,
    parse_log,
    parse_log_text,
    parse_meas_lines,
)

LOGS = Path(__file__).resolve().parent / "fixtures" / "logs"


def _read(name: str) -> str:
    return (LOGS / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# .op + .meas parsing
# ---------------------------------------------------------------------------


def test_parse_meas_lines_extracts_meas_values() -> None:
    text = _read("rc_lowpass_tran_ok.log")
    out = parse_meas_lines(text)
    assert out["vout_max"] == pytest.approx(0.70710678)
    assert out["vout_min"] == pytest.approx(-0.70710678)
    assert out["vout_avg"] == pytest.approx(4.2e-05)
    assert out["vout_pp"] == pytest.approx(1.41421356)
    assert out["vout_rms"] == pytest.approx(0.5001245)


def test_parse_log_text_extracts_op_meas_and_op_variables() -> None:
    text = _read("rc_lowpass_op_ok.log")
    report = parse_log_text(text)
    # .op variables
    assert "v(in)" in report.measurements
    assert report.measurements["v(in)"].value == pytest.approx(1.0)
    assert "v(out)" in report.measurements
    assert report.measurements["v(out)"].value == pytest.approx(0.5)
    # current with SI suffix
    assert "i(R1)" in report.measurements
    assert report.measurements["i(R1)"].value == pytest.approx(5e-4)
    # .meas FIND
    assert "vout" in report.measurements
    assert report.measurements["vout"].value == pytest.approx(0.5)
    assert report.measurements["vout"].function == "FIND"


def test_parse_log_text_handles_voltage_divider_op() -> None:
    text = _read("voltage_divider_op_ok.log")
    report = parse_log_text(text)
    assert report.measurements["v(in)"].value == pytest.approx(12.0)
    assert report.measurements["v(out)"].value == pytest.approx(5.0)
    assert report.measurements["i(R1)"].value == pytest.approx(7e-4)
    assert report.measurements["i(R2)"].value == pytest.approx(5e-4)


def test_parse_log_text_handles_find_at_form() -> None:
    text = "Title: *\nvout: FIND(v(out))=0.5 AT 0\nElapsed time: 0.01 seconds.\n"
    report = parse_log_text(text)
    assert "vout" in report.measurements
    assert report.measurements["vout"].function == "FIND"
    assert report.measurements["vout"].value == pytest.approx(0.5)
    assert report.simulation_finished is True


def test_parse_log_text_handles_i_r1_with_si_suffix() -> None:
    text = "i(R1)=1m\ni(R2)=100u\nElapsed time: 0.01 seconds.\n"
    report = parse_log_text(text)
    assert report.measurements["i(R1)"].value == pytest.approx(1e-3)
    assert report.measurements["i(R2)"].value == pytest.approx(100e-6)


# ---------------------------------------------------------------------------
# Error / warning / fatal pattern detection
# ---------------------------------------------------------------------------


def test_parse_log_detects_fatal_error() -> None:
    text = _read("simulation_fatal.log")
    report = parse_log_text(text)
    codes = [f.code for f in report.findings]
    assert LOG_ERR_FATAL in codes
    assert LOG_ERR_MODEL in codes
    assert LOG_ERR_ERROR in codes
    assert LOG_ERR_TERMINATION in codes
    assert report.has_fatal is True
    assert report.is_simulation_success is False


def test_parse_log_detects_singular_matrix_and_timestep() -> None:
    text = _read("simulation_convergence.log")
    report = parse_log_text(text)
    codes = [f.code for f in report.findings]
    assert LOG_ERR_SINGULAR in codes
    assert LOG_ERR_TIMESTEP in codes
    assert LOG_ERR_TERMINATION in codes
    # Even though vout_max is 0.0, the simulation is still a failure.
    assert "vout_max" in report.measurements
    assert report.is_simulation_success is False


def test_parse_log_keeps_partial_measurements_on_failure() -> None:
    """LTspice can produce measurements on a converged-but-failed run;
    we keep them but the simulation is not successful."""
    text = _read("simulation_convergence.log")
    report = parse_log_text(text)
    assert report.measurements["vout_max"].value == pytest.approx(0.0)
    # Result-level "success" must be False; partial measurements are
    # still surfaced for debugging.
    assert report.is_simulation_success is False


def test_parse_log_detects_warning_without_fatal() -> None:
    text = _read("simulation_warnings.log")
    report = parse_log_text(text)
    assert report.has_fatal is False
    assert report.is_simulation_success is True
    assert any(f.code == LOG_ERR_WARNING for f in report.findings)
    # warnings are separated from errors
    assert [f.code for f in report.errors] == []
    assert [f.code for f in report.warnings] == [LOG_ERR_WARNING]


def test_parse_log_recognises_subcircuit_error() -> None:
    text = "Title: *\nFatal Error: Unknown subcircuit LM358 in call from X1\n"
    report = parse_log_text(text)
    assert report.has_fatal is True
    assert any(f.code == LOG_ERR_SUBCKT for f in report.findings)


def test_parse_log_recognises_parse_error() -> None:
    text = "Title: *\nERROR: parse error on line 12\n"
    report = parse_log_text(text)
    assert any(f.code == LOG_ERR_ERROR for f in report.findings)
    assert any(f.code == LOG_ERR_PARSE for f in report.findings)


# ---------------------------------------------------------------------------
# State and helpers
# ---------------------------------------------------------------------------


def test_parse_log_text_returns_empty_report_for_empty_input() -> None:
    report = parse_log_text("")
    assert report.line_count == 0
    assert report.measurements == {}
    assert report.findings == []
    assert report.simulation_finished is False


def test_parse_log_text_counts_non_empty_lines() -> None:
    text = "\nTitle: *\n\nElapsed time: 0.01 seconds.\n\n"
    report = parse_log_text(text)
    # Non-empty lines are "Title: *" and "Elapsed time: 0.01 seconds.".
    # Leading, trailing and inner blank lines are not counted.
    assert report.line_count == 2


def test_parse_log_reads_from_disk(tmp_path: Path) -> None:
    src = tmp_path / "x.log"
    src.write_text(_read("rc_lowpass_tran_ok.log"), encoding="utf-8")
    report = parse_log(src)
    assert "vout_max" in report.measurements


def test_parse_log_raises_on_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        parse_log(tmp_path / "nope.log")


def test_findings_to_errors_shape() -> None:
    findings = [
        LogFinding("LTSPICE_FATAL", 12, "Fatal Error: boom"),
    ]
    out = findings_to_errors(findings)
    assert out == [
        {
            "code": "LTSPICE_FATAL",
            "detail": "Fatal Error: boom",
            "data": {"lineNo": 12},
        }
    ]


def test_merge_measurements_last_wins() -> None:
    a = ParseReport(
        measurements={"x": log_parser.MeasurementResult(name="x", value=1.0, raw="1.0")}
    )
    b = ParseReport(
        measurements={"x": log_parser.MeasurementResult(name="x", value=2.0, raw="2.0")}
    )
    merged = merge_measurements(a, b)
    assert merged["x"].value == 2.0


def test_merge_measurements_handles_empty() -> None:
    assert merge_measurements() == {}


def test_parse_report_to_dict_shape() -> None:
    text = _read("rc_lowpass_tran_ok.log")
    report = parse_log_text(text)
    d = report.to_dict()
    assert d["schemaVersion"] == log_parser.PARSER_SCHEMA_VERSION
    assert "measurements" in d
    assert "findings" in d
    assert d["hasFatal"] is False
    assert d["simulationFinished"] is True
    assert d["lineCount"] > 0


def test_measurement_result_to_dict_shape() -> None:
    m = log_parser.MeasurementResult(
        name="vout_max",
        value=0.707,
        raw="0.707",
        function="MAX",
        expression="v(out)",
    )
    d = m.to_dict()
    assert d["name"] == "vout_max"
    assert d["value"] == 0.707
    assert d["function"] == "MAX"


def test_pydantic_ir_identifier_pattern_matches_parser_names() -> None:
    """Measurement names from fixtures must match the IR identifier
    pattern so the parser and IR layer agree on what is a safe name."""
    text = _read("rc_lowpass_tran_ok.log")
    report = parse_log_text(text)
    # IR pattern: ^[A-Za-z][A-Za-z0-9_]*$
    import re

    pat = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
    for name in report.measurements:
        assert pat.match(name), name
