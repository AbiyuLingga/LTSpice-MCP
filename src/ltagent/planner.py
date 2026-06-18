"""Phase 8: Rule-based planner for natural-language Circuit IR generation.

Per the engineering plan section 16, this phase adds a *deterministic* parser
for a small, well-defined set of MVP prompts in English and Indonesian. It
does not call an LLM, does not parse arbitrary English, and does not attempt
to handle ambiguous phrasing. If a prompt is not recognized, the planner
returns a structured :class:`PlannerRefusal` so the CLI layer can present
the supported prompt formats.

Design rules:

* Pure functions only. No filesystem I/O. No subprocess. The planner's
  sole responsibility is ``str -> CircuitIR | PlannerRefusal``.
* All math is double-precision ``float``. SPICE value strings are formatted
  with sensible engineering suffixes (``1.59k``, ``100n``) so the resulting
  ``.cir`` file is human-readable.
* The planner never invents a project name from nothing: it builds the
  name from the topology and the parsed parameters, all lower-case, all
  safe-slug.
* Refusal objects are stable so the CLI layer and downstream tools can
  switch on :attr:`PlannerRefusal.code`.

Supported MVP prompts (per plan section 16.1):

================================== =========================================
Prompt                              Resulting topology
================================== =========================================
``buat pembagi tegangan 12V ke 5V`` ``voltage_divider``
``make voltage divider 12V to 5V``  ``voltage_divider``
``buat RC low-pass cutoff 1kHz``    ``rc_lowpass``
``dengan C 100nF``                  (adds explicit capacitance)
``buat RC high-pass cutoff 500Hz``  ``rc_highpass``
================================== =========================================

Output (success):

``CircuitIR`` (from :mod:`ltagent.ir`) — round-trippable to JSON via
:func:`ltagent.ir.dump_ir`. Pass the dict form through ``ltagent.ir.validate_dict``
or ``CircuitIR.model_validate`` before persisting.

Output (refusal):

::

    PlannerRefusal(
        code="UNSUPPORTED_PROMPT",
        message="...",
        supported_topologies=[...],
        next_step="...",
        data={...},
    )

Out of scope for Phase 8 (deliberately):

* Free-form English / Indonesian via an LLM.
* Multi-stage conversations or prompt refinement.
* Templates with components beyond the MVP set.
* Free-form resistor or capacitor values without an explicit unit.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from .ir import (
    MVP_TOPOLOGIES,
    SCHEMA_VERSION,
    Analysis,
    AnalysisKind,
    CircuitIR,
    Component,
    ComponentKind,
    Constraints,
    Measurement,
    Metadata,
)
from .units import parse_spice_value

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Topology identifiers (mirrors ``ir.MVP_TOPOLOGIES`` plus the field name on
#: the IR). Kept here as a tuple so we can list them in a deterministic
#: order when reporting refusals.
_TOPOLOGY_ORDER: Final[tuple[str, ...]] = (
    "voltage_divider",
    "rc_lowpass",
    "rc_highpass",
)

#: Refusal codes (stable identifiers; switch on these in tooling).
REFUSAL_UNSUPPORTED_PROMPT: Final[str] = "UNSUPPORTED_PROMPT"
REFUSAL_MISSING_PARAM: Final[str] = "MISSING_PARAM"
REFUSAL_INVALID_VALUE: Final[str] = "INVALID_VALUE"
REFUSAL_AMBIGUOUS_PROMPT: Final[str] = "AMBIGUOUS_PROMPT"

#: Default total resistance for a voltage divider when only Vin/Vout are
#: given (ohms). 10k is the canonical "EE textbook" choice.
DEFAULT_DIVIDER_R2_OHMS: Final[float] = 1_000.0

#: Default capacitance for an RC filter when only fc is given (farads).
DEFAULT_RC_CAPACITANCE_F: Final[float] = 100e-9

#: Default tran stop-time for an RC filter when only fc is given (seconds).
#: 5 periods of the cutoff frequency is enough to see the steady-state
#: response settle.
DEFAULT_RC_TRAN_SECONDS_FACTOR: Final[float] = 5.0

#: Default AC stop-frequency for an RC filter when only fc is given. One
#: decade above the cutoff is enough to see the rolloff.
DEFAULT_RC_AC_STOP_FACTOR: Final[float] = 10.0

#: Decades per AC sweep.
DEFAULT_RC_AC_POINTS_PER_DECADE: Final[int] = 20

#: Default SINE amplitude for RC filter input (volts peak).
DEFAULT_RC_SINE_AMPLITUDE: Final[float] = 1.0

# ---------------------------------------------------------------------------
# Refusal object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannerRefusal:
    """Structured refusal returned by :func:`plan_prompt`.

    Mirrors the JSON shape in plan section 16.2. Frozen so callers cannot
    mutate the structured output after the planner returns it.
    """

    code: str
    message: str
    supported_topologies: tuple[str, ...]
    next_step: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable dict for the CLI output contract."""
        return {
            "code": self.code,
            "message": self.message,
            "supportedTopologies": list(self.supported_topologies),
            "nextStep": self.next_step,
            "data": dict(self.data),
        }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def plan_prompt(prompt: str) -> CircuitIR | PlannerRefusal:
    """Parse a natural-language prompt and return a ``CircuitIR`` or refusal.

    The function is total: every input string yields exactly one result.
    It never raises on user-visible errors. Internal programmer errors
    (e.g. an IR validator failure that contradicts the planner) still
    propagate.
    """
    if not isinstance(prompt, str):  # defensive; never expected in practice
        raise TypeError(f"prompt must be str, got {type(prompt).__name__}")
    text = _normalize(prompt)
    if not text:
        return PlannerRefusal(
            code=REFUSAL_UNSUPPORTED_PROMPT,
            message="Prompt is empty",
            supported_topologies=_TOPOLOGY_ORDER,
            next_step=(
                "Provide a prompt in English or Indonesian, e.g. "
                "'make voltage divider 12V to 5V' or 'buat RC low-pass "
                "cutoff 1kHz dengan C 100nF'."
            ),
            data={"rawPrompt": prompt},
        )

    topology = _detect_topology(text)
    if topology is None:
        return _unsupported_refusal(text)

    if topology == "voltage_divider":
        return _plan_voltage_divider(text)
    return _plan_rc(text, topology)


# ---------------------------------------------------------------------------
# Topology detection
# ---------------------------------------------------------------------------

# Each entry: (topology, regex). The regex is matched against the normalized
# (lower-case, dash-normalized) prompt. The first match wins, so put the
# most specific patterns first.
#
# We use word-ish boundaries (``\b``) liberally. The dashed forms are matched
# because the normalizer converts ``-`` to space; but we also keep raw
# hyphenated forms to be safe.
_TOPOLOGY_PATTERNS: Final[tuple[tuple[str, str], ...]] = (
    (
        "rc_highpass",
        r"\b(rc\s*high[\s-]?pass|high[\s-]?pass\s+filter|filter\s+lolos\s+tinggi)\b",
    ),
    (
        "rc_lowpass",
        r"\b(rc\s*low[\s-]?pass|low[\s-]?pass\s+filter|filter\s+lolos\s+rendah)\b",
    ),
    (
        "voltage_divider",
        r"\b(pembagi\s+tegangan|voltage\s+divider|resistive\s+divider)\b",
    ),
)


def _detect_topology(text: str) -> str | None:
    """Return the matching MVP topology name or ``None``."""
    for topology, pattern in _TOPOLOGY_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return topology
    return None


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

# Map unicode dashes / lookalikes to ASCII space so downstream patterns can
# use a single canonical form.
_DASH_CHARS: Final[str] = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
# Map unicode multiplication / micro / omega to ASCII so suffix regexes work.
_GREEK_CHARS: Final[Mapping[str, str]] = {
    "\u00b5": "u",  # µ (micro) -> u (SPICE convention)
    "\u03bc": "u",  # Greek small letter mu -> u
    "\u03a9": "k",  # Ω (omega) handled separately below if unit suffix
}
# Lower-case unicode omega is rare; keep the table small.
_OMEGA_CHARS: Final[str] = "\u03a9\u2126"


def _normalize(prompt: str) -> str:
    """Lower-case, strip, and collapse punctuation so regexes see one form."""
    s = prompt.strip()
    if not s:
        return ""
    for ch in _DASH_CHARS:
        s = s.replace(ch, " ")
    # Replace Ω with "k" placeholder for ohm? No: we want to match "kΩ" too,
    # so we replace Ω with the literal string "ohm" only when it is a unit
    # suffix (preceded by k, M, or digit). To keep it simple we do nothing
    # to Ω; the ohm-suffix regex below includes Ω explicitly. We also strip
    # the standalone omega character.
    for ch in _OMEGA_CHARS:
        s = s.replace(ch, "ohm")
    for src, dst in _GREEK_CHARS.items():
        s = s.replace(src, dst)
    # Collapse multiple whitespace to one space; lowercase for matching.
    s = re.sub(r"\s+", " ", s).lower()
    # Strip surrounding quotes users sometimes paste in (straight + curly
    # double and single quotes).
    s = s.strip("\"'`\u201c\u201d\u2018\u2019")
    return s


# ---------------------------------------------------------------------------
# Unit extractors
# ---------------------------------------------------------------------------

# Voltage: "12V", "12 V", "12v", "12 volt", "12 volts", "12mV", etc.
_VOLT_PATTERN: Final[str] = (
    r"(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>(?:milli)?v(?:olt)?s?)"
)
_VOLT_RE: Final[re.Pattern[str]] = re.compile(r"\b" + _VOLT_PATTERN + r"\b", re.IGNORECASE)

# Frequency: Hz, kHz, MHz, GHz (and the long Indonesian forms).
_FREQ_UNIT_PATTERN: Final[str] = (
    r"(?:g|m|k)?hz|"
    r"(?:giga|mega|kilo)?hertz|"
    r"(?:giga|mega|kilo)herz"
)
_FREQ_PATTERN: Final[str] = rf"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>{_FREQ_UNIT_PATTERN})"
_FREQ_RE: Final[re.Pattern[str]] = re.compile(r"\b" + _FREQ_PATTERN + r"\b", re.IGNORECASE)

# Capacitance: F, mF, uF, nF, pF (also spelled microfarad, nanofarad, etc.).
# We accept the SPICE suffix form (``u``, ``n``, ``p``, ``m``) directly in
# addition to the long forms.
_CAP_UNIT_PATTERN: Final[str] = (
    r"(?:milli|micro|nano|pico|u|n|p|m)?f(?:arad)?s?"
)
_CAP_PATTERN: Final[str] = rf"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>{_CAP_UNIT_PATTERN})"
_CAP_RE: Final[re.Pattern[str]] = re.compile(r"\b" + _CAP_PATTERN + r"\b", re.IGNORECASE)

# Resistance: ohm/kohm/Mohm (also Ω), and bare SPICE-style "1.59k".
# We treat k/Meg/M as resistance multipliers only when there is a unit
# marker (ohm, kΩ, etc.) OR when the bare value appears after "R ".
_RES_UNIT_PATTERN: Final[str] = (
    r"(?:mega|kilo)?ohms?|"
    r"kohm|mohm|"
    r"\u2126|"
    r"k\u2126|m\u2126"
)
# Bare SPICE resistance values look like "1.59k", "10k", "4.7meg".
_RES_BARE_PATTERN: Final[str] = r"\b(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>(?:meg|k|m)?)(?=\s|$)"

# What we look for in RC prompts that mentions "C" or "R" specifically. The
# prompt may use "C 100nF" or "R 1.59k". We extract the noun -> value/unit
# pair, then look up the unit to decide ohms vs farads.
_RC_PARAM_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?P<which>[cr])\s+(?P<rest>\d+(?:\.\d+)?\s*\S*)",
    re.IGNORECASE,
)


# Unit multipliers in hertz
_FREQ_MULTIPLIERS: Final[Mapping[str, float]] = {
    "hz": 1.0,
    "hertz": 1.0,
    "herz": 1.0,
    "khz": 1e3,
    "kilociclos": 1e3,  # not standard, but tolerant
    "kHz": 1e3,
    "kilohertz": 1e3,
    "kiloherz": 1e3,
    "mhz": 1e6,
    "megahertz": 1e6,
    "megaherz": 1e6,
    "ghz": 1e9,
    "gigahertz": 1e9,
    "gigaherz": 1e9,
}

_CAP_MULTIPLIERS: Final[Mapping[str, float]] = {
    "f": 1.0,
    "farad": 1.0,
    "farads": 1.0,
    "mf": 1e-3,
    "millifarad": 1e-3,
    "millifarads": 1e-3,
    "uf": 1e-6,
    "microfarad": 1e-6,
    "microfarads": 1e-6,
    "nf": 1e-9,
    "nanofarad": 1e-9,
    "nanofarads": 1e-9,
    "pf": 1e-12,
    "picofarad": 1e-12,
    "picofarads": 1e-12,
}

_VOLT_MULTIPLIERS: Final[Mapping[str, float]] = {
    "v": 1.0,
    "volt": 1.0,
    "volts": 1.0,
    "mv": 1e-3,
    "millivolt": 1e-3,
    "millivolts": 1e-3,
}


def _volt_to_si(text_unit: str) -> float | None:
    """Convert a matched voltage unit string to a multiplier (V -> 1.0)."""
    u = text_unit.lower().replace(" ", "")
    if u in _VOLT_MULTIPLIERS:
        return _VOLT_MULTIPLIERS[u]
    return None


def _freq_to_si(text_unit: str) -> float | None:
    u = text_unit.lower().replace(" ", "")
    if u in _FREQ_MULTIPLIERS:
        return _FREQ_MULTIPLIERS[u]
    return None


def _cap_to_si(text_unit: str) -> float | None:
    u = text_unit.lower().replace(" ", "")
    if u in _CAP_MULTIPLIERS:
        return _CAP_MULTIPLIERS[u]
    return None


# ---------------------------------------------------------------------------
# Number parsing + SPICE value formatting
# ---------------------------------------------------------------------------


def _parse_voltage(match: re.Match[str]) -> float | None:
    val = float(match.group("value"))
    mult = _volt_to_si(match.group("unit"))
    if mult is None:
        return None
    return val * mult


def _parse_frequency(match: re.Match[str]) -> float | None:
    val = float(match.group("value"))
    mult = _freq_to_si(match.group("unit"))
    if mult is None:
        return None
    return val * mult


def _parse_capacitance(match: re.Match[str]) -> float | None:
    val = float(match.group("value"))
    mult = _cap_to_si(match.group("unit"))
    if mult is None:
        return None
    return val * mult


def _parse_resistance_spice(text: str) -> float | None:
    """Parse a bare SPICE-style resistance literal like ``1.59k`` or ``4.7meg``.

    Returns ohms or ``None``. We delegate to :func:`parse_spice_value` for
    suffix parsing but restrict the allowed unit set so the function is not
    ambiguous about the quantity.
    """
    parsed = parse_spice_value(text.strip())
    if parsed is None or parsed < 0:
        return None
    # Cap at a sane upper bound to refuse obvious nonsense.
    if parsed > 1e15:
        return None
    return float(parsed)


def _parse_resistance_ohm(match_or_text: re.Match[str] | str) -> float | None:
    """Parse a resistance value carrying an explicit ``ohm`` / ``kohm`` unit.

    ``match_or_text`` is either a regex match (with ``value``/``unit``
    groups) or a bare text like ``"1.59k"`` interpreted as a SPICE literal.
    """
    if isinstance(match_or_text, re.Match):
        val = float(match_or_text.group("value"))
        unit = match_or_text.group("unit").lower().replace(" ", "")
        multipliers = {
            "ohm": 1.0,
            "ohms": 1.0,
            "kohm": 1e3,
            "kohms": 1e3,
            "mohm": 1e6,
            "mohms": 1e6,
            "megohm": 1e6,
            "megohms": 1e6,
            "k\u2126": 1e3,
            "m\u2126": 1e6,
            "\u2126": 1.0,
        }
        mult = multipliers.get(unit)
        if mult is None:
            return None
        return val * mult
    return _parse_resistance_spice(match_or_text)


# Map a numeric value to the most readable SPICE suffix for a given quantity.
def _format_resistance_spice(ohms: float) -> str:
    """Format ohms as a SPICE-style literal (``1.59k``)."""
    if ohms <= 0:
        raise ValueError(f"resistance must be > 0, got {ohms}")
    return _format_si(ohms, resistance=True)


def _format_capacitance_spice(farads: float) -> str:
    """Format farads as a SPICE-style literal (``100n``)."""
    if farads <= 0:
        raise ValueError(f"capacitance must be > 0, got {farads}")
    return _format_si(farads, resistance=False)


def _format_time_spice(seconds: float) -> str:
    """Format seconds as a SPICE-style literal (``5m``)."""
    if seconds <= 0:
        raise ValueError(f"time must be > 0, got {seconds}")
    return _format_si(seconds, resistance=False)


def _format_freq_spice(hertz: float) -> str:
    """Format hertz as a SPICE-style literal (``1k``)."""
    if hertz <= 0:
        raise ValueError(f"frequency must be > 0, got {hertz}")
    return _format_si(hertz, resistance=False)


# Suffix chains ordered from largest to smallest multiplier. We pick the
# first chain entry whose scaled value lands in ``[1, 1000)``.
_SUFFIX_CHAIN_RESISTANCE: Final[tuple[tuple[float, str], ...]] = (
    (1e9, "g"),
    (1e6, "meg"),
    (1e3, "k"),
    (1.0, ""),
    (1e-3, "m"),
)
_SUFFIX_CHAIN_TIME_CAP: Final[tuple[tuple[float, str], ...]] = (
    (1e9, "g"),
    (1e6, "meg"),
    (1e3, "k"),
    (1.0, ""),
    (1e-3, "m"),
    (1e-6, "u"),
    (1e-9, "n"),
    (1e-12, "p"),
    (1e-15, "f"),
)


def _format_si(value: float, *, resistance: bool) -> str:
    """Return ``value`` formatted with the largest unit suffix that keeps the
    coefficient in ``[1, 1000)``. For resistance we use ``meg`` (LTspice's
    spelling, not ``m`` which means milli)."""
    if value <= 0:
        return "0"
    chain = _SUFFIX_CHAIN_RESISTANCE if resistance else _SUFFIX_CHAIN_TIME_CAP
    for scale, suffix in chain:
        scaled = value / scale
        if 1.0 <= scaled < 1000.0:
            return f"{_format_scaled(scaled)}{suffix}"
    # Fall through (e.g. extremely large or extremely small value); use the
    # smallest scale in the chain so we keep a meaningful coefficient.
    scale, suffix = chain[-1]
    return f"{_format_scaled(value / scale)}{suffix}"


def _format_scaled(scaled: float) -> str:
    """Format a coefficient in ``[1, 1000)`` with up to 3 significant digits.

    Trims trailing zeros. Uses general format (``g``) for sub-1 values so
    we never collapse to ``0``.
    """
    if scaled >= 100:
        return f"{scaled:.0f}"
    if scaled >= 10:
        return f"{scaled:.2f}".rstrip("0").rstrip(".")
    if scaled >= 1:
        return f"{scaled:.3f}".rstrip("0").rstrip(".")
    return f"{scaled:g}"


# ---------------------------------------------------------------------------
# Voltage divider planning
# ---------------------------------------------------------------------------


def _plan_voltage_divider(text: str) -> CircuitIR | PlannerRefusal:
    volts = list(_VOLT_RE.finditer(text))
    if not volts:
        return PlannerRefusal(
            code=REFUSAL_MISSING_PARAM,
            message="Voltage divider requires two voltage values (Vin and Vout)",
            supported_topologies=_TOPOLOGY_ORDER,
            next_step=(
                "Add both voltages, e.g. 'make voltage divider 12V to 5V' or "
                "'buat pembagi tegangan 12V ke 5V'."
            ),
            data={"prompt": text},
        )
    if len(volts) > 2:
        # Could happen with stray V mentions. Keep the first two.
        volts = volts[:2]

    vin_match = volts[0]
    vout_match = volts[1] if len(volts) >= 2 else None

    vin = _parse_voltage(vin_match)
    vout = _parse_voltage(vout_match) if vout_match else None

    if vin is None or vout is None:
        return PlannerRefusal(
            code=REFUSAL_INVALID_VALUE,
            message="Could not parse one of the voltage values",
            supported_topologies=_TOPOLOGY_ORDER,
            next_step=(
                "Use a numeric voltage with a unit, e.g. '12V', '5V', '3.3V'."
            ),
            data={"prompt": text, "vinRaw": vin_match.group(0)},
        )
    if vin <= 0:
        return _invalid("Vin must be > 0", text, vin=vin)
    if vout <= 0:
        return _invalid("Vout must be > 0", text, vout=vout)
    if vout >= vin:
        return _invalid("Vout must be less than Vin", text, vin=vin, vout=vout)

    r2 = DEFAULT_DIVIDER_R2_OHMS
    r1 = r2 * (vin - vout) / vout

    r1_spice = _format_resistance_spice(r1)
    r2_spice = _format_resistance_spice(r2)

    name = _safe_name(
        f"voltage_divider_{_si_tag(vin, 'V')}_to_{_si_tag(vout, 'V')}",
        fallback="voltage_divider",
    )

    components = [
        Component(
            id="Vin",
            kind=ComponentKind.VOLTAGE_SOURCE,
            spicePrefix="V",
            nodes=["in", "0"],
            value=f"DC {vin:g}",
            role="input_source",
        ),
        Component(
            id="R1",
            kind=ComponentKind.RESISTOR,
            spicePrefix="R",
            nodes=["in", "out"],
            value=r1_spice,
            role="series_resistor",
        ),
        Component(
            id="R2",
            kind=ComponentKind.RESISTOR,
            spicePrefix="R",
            nodes=["out", "0"],
            value=r2_spice,
            role="shunt_resistor",
        ),
    ]
    analysis = [Analysis(kind=AnalysisKind.OP)]
    measurements = [
        Measurement(
            name="VOUT",
            analysis=AnalysisKind.OP,
            expression="V(out)",
        ),
    ]
    probes = ["V(in)", "V(out)"]
    constraints = Constraints.model_validate({"vin": vin, "targetVout": vout})
    metadata = Metadata.model_validate({"createdBy": "ltagent", "source": "planner"})

    return CircuitIR(
        schemaVersion=SCHEMA_VERSION,
        name=name,
        topology="voltage_divider",
        description=(
            f"Resistive voltage divider from {vin:g}V to ~{vout:g}V using "
            f"R1={r1_spice}, R2={r2_spice}."
        ),
        nodes=["in", "out", "0"],
        components=components,
        analysis=analysis,
        measurements=measurements,
        probes=probes,
        directives=[],
        constraints=constraints,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# RC filter planning
# ---------------------------------------------------------------------------


def _plan_rc(text: str, topology: str) -> CircuitIR | PlannerRefusal:
    fc = _extract_first_frequency(text)
    cap = _extract_rc_capacitance(text)
    res = _extract_rc_resistance(text)

    if fc is None:
        return PlannerRefusal(
            code=REFUSAL_MISSING_PARAM,
            message=(
                "RC filter requires a cutoff frequency, "
                "e.g. 'cutoff 1kHz' or 'fc 500Hz'."
            ),
            supported_topologies=_TOPOLOGY_ORDER,
            next_step=(
                "Add a cutoff frequency, e.g. 'buat RC low-pass cutoff 1kHz'."
            ),
            data={"prompt": text, "topology": topology},
        )
    if fc <= 0:
        return _invalid("Cutoff frequency must be > 0", text, fc=fc)

    if cap is None and res is None:
        cap = DEFAULT_RC_CAPACITANCE_F
    if cap is not None and res is None:
        # Compute R from fc and C.
        res = 1.0 / (2.0 * math.pi * fc * cap)
    elif res is not None and cap is None:
        cap = 1.0 / (2.0 * math.pi * fc * res)
    elif cap is not None and res is not None:
        # Both given; prefer the explicit capacitance (smaller refactor).
        res = 1.0 / (2.0 * math.pi * fc * cap)

    if res is None or cap is None:
        return _invalid("Could not derive a resistance or capacitance", text)
    if res <= 0 or cap <= 0:
        return _invalid(
            "Computed RC values are non-positive", text, resistance=res, capacitance=cap
        )

    r_spice = _format_resistance_spice(res)
    c_spice = _format_capacitance_spice(cap)

    name = _safe_name(
        f"{topology}_{_si_tag(fc, 'Hz')}_c{_si_tag(cap, 'F')}",
        fallback=topology,
    )

    if topology == "rc_lowpass":
        series_id, series_kind = "R1", ComponentKind.RESISTOR
        series_spice = r_spice
        shunt_id, shunt_kind = "C1", ComponentKind.CAPACITOR
        shunt_spice = c_spice
        series_role = "series_resistor"
        shunt_role = "shunt_capacitor"
        series_nodes = ["in", "out"]
        shunt_nodes = ["out", "0"]
    else:  # rc_highpass
        series_id, series_kind = "C1", ComponentKind.CAPACITOR
        series_spice = c_spice
        shunt_id, shunt_kind = "R1", ComponentKind.RESISTOR
        shunt_spice = r_spice
        series_role = "series_capacitor"
        shunt_role = "shunt_resistor"
        series_nodes = ["in", "out"]
        shunt_nodes = ["out", "0"]

    series_prefix = {"resistor": "R", "capacitor": "C"}[series_kind.value]
    shunt_prefix = {"resistor": "R", "capacitor": "C"}[shunt_kind.value]

    components = [
        Component(
            id="Vin",
            kind=ComponentKind.VOLTAGE_SOURCE,
            spicePrefix="V",
            nodes=["in", "0"],
            value=_sine_source(fc),
            role="input_source",
        ),
        Component(
            id=series_id,
            kind=series_kind,
            spicePrefix=series_prefix,
            nodes=series_nodes,
            value=series_spice,
            role=series_role,
        ),
        Component(
            id=shunt_id,
            kind=shunt_kind,
            spicePrefix=shunt_prefix,
            nodes=shunt_nodes,
            value=shunt_spice,
            role=shunt_role,
        ),
    ]

    tran_stop = DEFAULT_RC_TRAN_SECONDS_FACTOR / fc
    ac_stop = DEFAULT_RC_AC_STOP_FACTOR * fc
    analysis = [
        Analysis(kind=AnalysisKind.TRAN, stopTime=_format_time_spice(tran_stop)),
        Analysis(
            kind=AnalysisKind.AC,
            stopFreq=_format_freq_spice(ac_stop),
            pointsPerDecade=DEFAULT_RC_AC_POINTS_PER_DECADE,
        ),
    ]
    measurements = [
        Measurement(
            name="VOUT_MAX",
            analysis=AnalysisKind.TRAN,
            expression="MAX V(out)",
        ),
    ]
    probes = ["V(in)", "V(out)"]
    constraints = Constraints.model_validate({"targetCutoffHz": fc})
    metadata = Metadata.model_validate({"createdBy": "ltagent", "source": "planner"})

    description = (
        f"First-order RC {'low' if topology == 'rc_lowpass' else 'high'}-pass "
        f"filter with cutoff ~{fc:g}Hz (R={r_spice}, C={c_spice})."
    )

    return CircuitIR(
        schemaVersion=SCHEMA_VERSION,
        name=name,
        topology=topology,
        description=description,
        nodes=["in", "out", "0"],
        components=components,
        analysis=analysis,
        measurements=measurements,
        probes=probes,
        directives=[],
        constraints=constraints,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Helpers used by RC planning
# ---------------------------------------------------------------------------


def _extract_first_frequency(text: str) -> float | None:
    """Return the first parsed frequency in Hz, or None."""
    for m in _FREQ_RE.finditer(text):
        hz = _parse_frequency(m)
        if hz is not None:
            return hz
    return None


def _extract_rc_capacitance(text: str) -> float | None:
    """Extract capacitance mentioned as ``C 100nF`` or as a bare F-unit value.

    The "C 100nF" form takes precedence; if absent, we look for any F-unit
    number in the prompt that is not preceded by ``R``.
    """
    for m in _RC_PARAM_RE.finditer(text):
        which = m.group("which").lower()
        if which != "c":
            continue
        rest = m.group("rest").strip()
        # Try as a capacitance first, then as a bare SPICE value (F-unit).
        cap_match = _CAP_RE.match(rest)
        if cap_match:
            cap = _parse_capacitance(cap_match)
            if cap is not None:
                return cap
        # Try to parse as a bare number with SPICE suffix; reject if it
        # looks more like a resistance (k/Meg ending).
        bare = _parse_resistance_spice(rest)
        if bare is not None and not _looks_like_resistance(rest):
            # Treat as farads. (10n -> 1e-8, fine; 100n -> 1e-7, fine.)
            return bare
    return None


def _extract_rc_resistance(text: str) -> float | None:
    """Extract resistance mentioned as ``R 1.59k`` or as a bare resistance."""
    for m in _RC_PARAM_RE.finditer(text):
        which = m.group("which").lower()
        if which != "r":
            continue
        rest = m.group("rest").strip()
        # First try ohm-suffix form.
        ohm_match = re.match(
            r"^(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>(?:mega|kilo)?ohms?|kohm|mohm|\u2126|k\u2126|m\u2126)$",
            rest,
            re.IGNORECASE,
        )
        if ohm_match:
            r = _parse_resistance_ohm(ohm_match)
            if r is not None:
                return r
        # Otherwise treat as a bare SPICE resistance literal.
        if _looks_like_resistance(rest):
            r = _parse_resistance_spice(rest)
            if r is not None:
                return r
    return None


def _looks_like_resistance(text: str) -> bool:
    """True if ``text`` ends with a resistance multiplier (k, Meg)."""
    s = text.strip().lower()
    return s.endswith("k") or s.endswith("meg")


def _sine_source(fc_hz: float) -> str:
    """Build a SPICE SINE() string for the Vin source.

    LTspice ``SINE(Voff Vamp Freq Td Theta Phi Ncycles)``. We use Voff=0,
    Vamp=1V, Freq=fc, Td=0 for simplicity. Ncycles is left at the default
    (infinite).
    """
    amp = DEFAULT_RC_SINE_AMPLITUDE
    return f"SINE(0 {amp:g} {_format_freq_spice(fc_hz)})"


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


def _unsupported_refusal(text: str) -> PlannerRefusal:
    return PlannerRefusal(
        code=REFUSAL_UNSUPPORTED_PROMPT,
        message=(
            "Prompt not recognized. Supported topologies: voltage_divider, "
            "rc_lowpass, rc_highpass."
        ),
        supported_topologies=_TOPOLOGY_ORDER,
        next_step=(
            "Provide a supported prompt such as 'make voltage divider 12V "
            "to 5V' or 'buat RC low-pass cutoff 1kHz dengan C 100nF', or "
            "supply a Circuit IR JSON file instead."
        ),
        data={"prompt": text, "supportedTopologies": list(MVP_TOPOLOGIES)},
    )


def _invalid(message: str, text: str, **extras: Any) -> PlannerRefusal:
    return PlannerRefusal(
        code=REFUSAL_INVALID_VALUE,
        message=message,
        supported_topologies=_TOPOLOGY_ORDER,
        next_step="Adjust the prompt so the relevant numeric values are valid.",
        data={"prompt": text, **extras},
    )


# Slug pattern reused from ir.py so generated names always validate.
_SLUG_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def _si_tag(value: float, unit: str) -> str:
    """Format a SI value as a slug-safe tag (``1khz``, ``100nf``, ``12v``).

    Fractional coefficients are rounded to integers in the tag so the slug
    stays valid (``79.58nf`` becomes ``80nf``).
    """
    if unit == "V":
        return _slug_from(_format_freq_spice(value)) + "v"
    if unit == "Hz":
        return _slug_from(_format_freq_spice(value)) + "hz"
    if unit == "F":
        return _slug_from(_format_capacitance_spice(value)) + "f"
    return _slug_from(_format_freq_spice(value))


def _slug_from(spice_text: str) -> str:
    """Round a SPICE literal's coefficient to an integer if it has a decimal.

    Examples: ``1.59k`` -> ``1k``; ``100n`` -> ``100n``; ``79.58n`` -> ``80n``.
    The result is always slug-safe (only ``[a-z0-9]`` after suffix handling).
    """
    # Split coefficient and suffix.
    m = re.match(r"^(?P<coef>\d+(?:\.\d+)?)(?P<suffix>[a-z]*)$", spice_text)
    if not m:
        return spice_text
    coef = m.group("coef")
    suffix = m.group("suffix")
    if "." in coef:
        coef = str(round(float(coef)))
    return f"{coef}{suffix}"


def _safe_name(candidate: str, *, fallback: str) -> str:
    """Ensure the candidate matches the IR project-name slug pattern."""
    # Replace any non-allowed chars with underscores.
    safe = re.sub(r"[^a-z0-9_-]", "_", candidate.lower())
    safe = re.sub(r"_+", "_", safe).strip("_-")
    if not safe or not _SLUG_RE.match(safe):
        return fallback
    return safe


__all__ = [
    "REFUSAL_AMBIGUOUS_PROMPT",
    "REFUSAL_INVALID_VALUE",
    "REFUSAL_MISSING_PARAM",
    "REFUSAL_UNSUPPORTED_PROMPT",
    "PlannerRefusal",
    "plan_prompt",
]
