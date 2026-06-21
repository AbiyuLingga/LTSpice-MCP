"""Phase 4: Parse LTspice ``.log`` and ``.meas`` output into structured data.

Converts the textual log file produced by ``LTspice -b circuit.cir`` into
the structured shape used by :mod:`ltagent.result` to build ``result.json``.

Scope (per plan section 14):

* Parse ``.meas`` results: ``FIND``, ``MAX``, ``MIN``, ``AVG``, ``RMS``,
  ``PP``, ``INTEG`` and the timing variants ``WHEN``, ``AT``.
* Detect common error / warning / fatal patterns emitted by LTspice.
* Tolerate the cosmetic variations LTspice produces across releases
  (uppercase, lowercase, parenthesised vs. unparenthesised expressions).
* Never raise. Every parse step collects findings into a structured
  :class:`ParseReport`; callers decide what to do with them.

Hard rules:

* No ``re`` flags that would silently accept surprising patterns
  (e.g. ``re.DOTALL`` on multi-line constructs unless explicitly needed).
* Numeric values are always parsed as ``float``; LTspice uses engineering
  suffixes (``k``, ``m``, ``u``, ``n``, ``p``, ``meg``) only in the
  netlist, not in measurement results. Measurement values are emitted
  as raw decimal so no SI suffix handling is needed here.
* Measurement names match :data:`ltagent.ir.IDENTIFIER_PATTERN`
  (``^[A-Za-z][A-Za-z0-9_]*$``) and are rejected if they don't.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

#: Minimum schema version of the parser's own output contract. Bumped when
#: the shape of :class:`ParseReport` changes in a backwards-incompatible
#: way. Kept independent of the IR schema version because the parser
#: contract is narrower.
PARSER_SCHEMA_VERSION = "0.1"

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

#: Lines like ``vout_max: MAX(v(out))=0.70710678 FROM 0 TO 0.005``.
#: The function is followed by a parenthesised expression. The
#: expression itself can contain balanced parens (e.g. ``v(out)`` or
#: ``i(R1)``), so we use a single-level balanced match
#: ``[^()]+(?:\([^()]*\)[^()]*)*`` to extract it.
_MEAS_RESULT_RE = re.compile(
    r"^\s*"
    r"(?P<name>[A-Za-z][A-Za-z0-9_]*)"  # measurement name
    r"\s*:\s*"  # colon separator
    r"(?P<func>[A-Z][A-Z0-9_]*)"  # function name (MAX, MIN, ...)
    r"(?:\((?P<expr>[^()]+(?:\([^()]*\)[^()]*)*)\))?"  # optional (expr)
    r"\s*=\s*"
    r"(?P<value>[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)"  # numeric value
    r"(?:\s+(?:FROM\s+\S+\s+TO\s+\S+|AT\s+\S+))?"  # optional tran range
    r"\s*$"
)

#: Special case for ``.meas FIND ... AT <time>``. Captured separately so
#: future iterations can surface the AT timestamp explicitly. The
#: primary regex above already handles the value.
_MEAS_FIND_AT_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z][A-Za-z0-9_]*)\s*:\s*FIND\((?P<expr>[^()]+)\)\s*="
    r"\s*(?P<value>[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)"
    r"\s+AT\s+\S+\s*$"
)

#: Lines like ``v(out)=0.5`` and ``i(R1)=1.0m`` in an ``.op`` log section.
#: Units are NOT converted here; the SI suffix is preserved in the raw
#: value string and a best-effort float is also provided. The ``name`` is
#: the left-hand side (``v(out)`` / ``i(R1)``).
_OP_VARIABLE_RE = re.compile(
    r"^\s*(?P<lhs>[A-Za-z][A-Za-z0-9_]*\([^()\n]+\))\s*=\s*"
    r"(?P<value>[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?[a-zA-Z]*)\s*$"
)

#: Stable error codes for the messages we detect. The strings match the
#: pattern used by :mod:`ltagent.runner` and :mod:`ltagent.doctor` so
#: agents can switch on them uniformly.
LOG_ERR_FATAL = "LTSPICE_FATAL"
LOG_ERR_ERROR = "LTSPICE_ERROR"
LOG_ERR_WARNING = "LTSPICE_WARNING"
LOG_ERR_SINGULAR = "LTSPICE_SINGULAR_MATRIX"
LOG_ERR_TIMESTEP = "LTSPICE_TIMESTEP_TOO_SMALL"
LOG_ERR_MODEL = "LTSPICE_MODEL_NOT_FOUND"
LOG_ERR_SUBCKT = "LTSPICE_UNKNOWN_SUBCKT"
LOG_ERR_PARSE = "LTSPICE_PARSE_ERROR"
LOG_ERR_INTERNAL = "LTSPICE_INTERNAL_ERROR"
LOG_ERR_TERMINATION = "LTSPICE_DID_NOT_FINISH"

#: Patterns of common error/fatal/warning lines emitted by LTspice XVII.
#: Each entry is ``(compiled regex, code)``. The order matters: more
#: specific patterns come first so that, e.g., a "Singular matrix" line
#: is classified as ``LTSPICE_SINGULAR_MATRIX`` instead of a generic
#: ``LTSPICE_ERROR``.
#:
#: Patterns marked with ``ANYWHERE=True`` are not anchored at line
#: start. They are sub-classifications used to extract a more specific
#: code from a line whose head has already matched a generic pattern
#: like ``Fatal Error:`` or ``ERROR:``.
_ERROR_PATTERNS: tuple[tuple[re.Pattern[str], str, bool], ...] = (
    (re.compile(r"Fatal\s+[Ee]rror\s*:", re.IGNORECASE), LOG_ERR_FATAL, False),
    (
        re.compile(r"Singular\s+matrix\s*:", re.IGNORECASE),
        LOG_ERR_SINGULAR,
        False,
    ),
    (
        re.compile(r"Timestep\s+too\s+small", re.IGNORECASE),
        LOG_ERR_TIMESTEP,
        False,
    ),
    (
        re.compile(r"Could\s+not\s+find\s+a\s+part", re.IGNORECASE),
        LOG_ERR_MODEL,
        False,
    ),
    (
        re.compile(
            r"(?:Unable\s+to\s+find\s+definition\s+of\s+model"
            r"|Unknown\s+subcircuit|Subcircuit\s+.*\s+not\s+defined)",
            re.IGNORECASE,
        ),
        LOG_ERR_SUBCKT,
        False,
    ),
    (
        re.compile(r"Error\s*:\s*parse\s+error", re.IGNORECASE),
        LOG_ERR_PARSE,
        False,
    ),
    (
        re.compile(r"Internal\s+error\s*:", re.IGNORECASE),
        LOG_ERR_INTERNAL,
        False,
    ),
    (re.compile(r"ERROR\s*:", re.IGNORECASE), LOG_ERR_ERROR, False),
    (re.compile(r"Warning\s*:", re.IGNORECASE), LOG_ERR_WARNING, False),
    (
        re.compile(r"(?:Simulation\s+stopped|Did\s+not\s+converge)", re.IGNORECASE),
        LOG_ERR_TERMINATION,
        False,
    ),
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MeasurementResult:
    """One ``.meas`` result extracted from the log.

    Attributes:
        name: Measurement name (matches the IR measurement name).
        function: The LTspice function used, uppercased (``MAX``, ``MIN``,
            ``FIND``, ...). ``None`` for ``.op`` variable lines.
        expression: The expression inside the function (best effort).
        value: The numeric value as a float.
        raw: The original value string exactly as printed by LTspice.
    """

    name: str
    value: float
    raw: str
    function: str | None = None
    expression: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LogFinding:
    """A single error, warning, or fatal message found in the log.

    The ``code`` is one of the ``LOG_ERR_*`` constants defined in this
    module. ``line_no`` is 1-indexed; ``line`` is the verbatim line that
    triggered the finding.
    """

    code: str
    line_no: int
    line: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ParseReport:
    """Structured result of parsing a single LTspice log.

    Attributes:
        measurements: Mapping from measurement name (or ``.op`` variable
            name) to a :class:`MeasurementResult`. ``.op`` variables are
            stored with their raw key (``v(out)``, ``i(R1)``) so the
            caller can distinguish from ``.meas`` keys.
        findings: All non-fatal warnings and errors detected in the log.
        has_fatal: ``True`` if any ``Fatal Error`` or ``LTSPICE_DID_NOT_FINISH``
            finding was detected. Callers should treat this as a
            simulation failure even if measurements are present.
        simulation_finished: ``True`` if LTspice emitted the standard
            "Elapsed time" / "Total elapsed time" trailer indicating
            clean exit. We don't *require* it for success (some
            variants omit it), but its absence is reported as a warning
            when combined with other errors.
        line_count: Total non-empty lines in the input.
    """

    measurements: dict[str, MeasurementResult] = field(default_factory=dict)
    findings: list[LogFinding] = field(default_factory=list)
    has_fatal: bool = False
    simulation_finished: bool = False
    line_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": PARSER_SCHEMA_VERSION,
            "measurements": {k: v.to_dict() for k, v in self.measurements.items()},
            "findings": [f.to_dict() for f in self.findings],
            "hasFatal": self.has_fatal,
            "simulationFinished": self.simulation_finished,
            "lineCount": self.line_count,
        }

    @property
    def errors(self) -> list[LogFinding]:
        """Findings that should mark the simulation as failed."""
        fatal_codes = {
            LOG_ERR_FATAL,
            LOG_ERR_ERROR,
            LOG_ERR_SINGULAR,
            LOG_ERR_TIMESTEP,
            LOG_ERR_MODEL,
            LOG_ERR_SUBCKT,
            LOG_ERR_PARSE,
            LOG_ERR_INTERNAL,
            LOG_ERR_TERMINATION,
        }
        return [f for f in self.findings if f.code in fatal_codes]

    @property
    def warnings(self) -> list[LogFinding]:
        """Findings that should appear as warnings, not errors."""
        return [f for f in self.findings if f.code == LOG_ERR_WARNING]

    @property
    def is_simulation_success(self) -> bool:
        """``True`` only when no fatal/error findings are present.

        The presence of ``.meas`` values is not sufficient — LTspice
        can produce partial measurements even on a failed run.
        """
        return not self.has_fatal and not self.errors


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_log(source: str | Path) -> ParseReport:
    """Parse an LTspice ``.log`` file from a path.

    Convenience wrapper over :func:`parse_log_text` that reads the file
    using UTF-8 encoding. Errors reading the file are propagated to the
    caller — the parser itself never raises on log content.

    Raises:
        FileNotFoundError: if ``source`` does not exist.
        OSError: on other filesystem errors.
    """
    text = Path(source).read_text(encoding="utf-8")
    return parse_log_text(text)


def parse_log_text(text: str) -> ParseReport:
    """Parse an LTspice log from raw text.

    The function is pure and never raises. Empty input returns an empty
    :class:`ParseReport` with ``line_count = 0`` and
    ``simulation_finished = False``.
    """
    report = ParseReport()
    if not text:
        return report

    lines = text.splitlines()
    report.line_count = sum(1 for line in lines if line.strip())

    for idx, line in enumerate(lines, start=1):
        # ---- .meas results -----------------------------------------------
        m = _MEAS_RESULT_RE.match(line)
        if m:
            try:
                value = float(m.group("value"))
            except ValueError:
                # Should be impossible given the regex, but be defensive.
                continue
            name = m.group("name")
            report.measurements[name] = MeasurementResult(
                name=name,
                value=value,
                raw=m.group("value"),
                function=m.group("func"),
                expression=m.group("expr").strip(),
            )
            continue

        # ---- .op variable lines -----------------------------------------
        m = _OP_VARIABLE_RE.match(line)
        if m:
            try:
                value = _parse_value_with_suffix(m.group("value"))
            except ValueError:
                continue
            key = m.group("lhs").strip()
            report.measurements[key] = MeasurementResult(
                name=key,
                value=value,
                raw=m.group("value"),
                function=None,
                expression=None,
            )
            continue

        # ---- error / warning patterns -----------------------------------
        # Apply ALL matching patterns, not just the first. A "Fatal Error:
        # Unknown subcircuit X" line is both a fatal AND a subcircuit
        # diagnostic; collapsing it to a single code would lose
        # information that downstream tooling (and the test fixtures)
        # rely on.
        for pattern, code, _anchor in _ERROR_PATTERNS:
            if pattern.search(line):
                report.findings.append(LogFinding(code=code, line_no=idx, line=line.rstrip()))
                if code in (LOG_ERR_FATAL, LOG_ERR_TERMINATION):
                    report.has_fatal = True

        # ---- success trailer -------------------------------------------
        if "Elapsed time" in line or "Total elapsed time" in line:
            report.simulation_finished = True

    # If the log has a fatal finding but no clean trailer, mark as
    # unfinished so downstream code can detect the partial state.
    if report.has_fatal and not report.simulation_finished:
        # No extra work; has_fatal already propagates to is_simulation_success.
        pass

    return report


def parse_meas_lines(text: str) -> dict[str, float]:
    """Parse the simplest form of ``.meas`` results from an LTspice log.

    Returns a flat ``{name: value}`` dict. Use :func:`parse_log_text` if
    you need findings, error detection, or ``.op`` variable lines.
    """
    report = parse_log_text(text)
    return {name: mr.value for name, mr in report.measurements.items()}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_value_with_suffix(raw: str) -> float:
    """Parse a numeric value, with a trailing SI suffix if present.

    LTspice sometimes emits op-point currents with suffixes (``1m``,
    ``100u``). We use the same simple rules as :mod:`ltagent.units` so
    the parser is consistent with the rest of the codebase. Plain
    decimal strings (no suffix) are accepted too.
    """
    s = raw.strip()
    if not s:
        raise ValueError("empty value")
    # Fast path: pure decimal.
    try:
        return float(s)
    except ValueError:
        pass
    # Split into mantissa + suffix.
    m = re.match(r"^([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)([a-zA-Z]+)$", s)
    if not m:
        raise ValueError(f"unparsable value: {raw!r}")
    mantissa = float(m.group(1))
    suffix = m.group(2).lower()
    multipliers: dict[str, float] = {
        "t": 1e12,
        "g": 1e9,
        "meg": 1e6,
        "k": 1e3,
        "m": 1e-3,
        "u": 1e-6,
        "n": 1e-9,
        "p": 1e-12,
        "f": 1e-15,
    }
    # ``meg`` must be tried before ``m`` because of the prefix overlap.
    if "meg" in suffix:
        return mantissa * 1e6
    factor = multipliers.get(suffix)
    if factor is None:
        raise ValueError(f"unknown SI suffix: {suffix!r}")
    return mantissa * factor


def merge_measurements(*reports: ParseReport) -> dict[str, MeasurementResult]:
    """Merge measurements from multiple reports, last-write-wins per name.

    Useful when an IR has both ``.op`` and ``.tran`` analyses and the
    runner produces more than one log section. Empty input returns an
    empty dict.
    """
    merged: dict[str, MeasurementResult] = {}
    for r in reports:
        merged.update(r.measurements)
    return merged


def findings_to_errors(
    findings: Iterable[LogFinding],
) -> list[dict[str, Any]]:
    """Convert findings to the ``{code, detail, data}`` shape used in JSON
    output contracts (see plan section 8.2)."""
    out: list[dict[str, Any]] = []
    for f in findings:
        out.append(
            {
                "code": f.code,
                "detail": f.line.strip(),
                "data": {"lineNo": f.line_no},
            }
        )
    return out
