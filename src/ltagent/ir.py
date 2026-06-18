"""Circuit IR v0.1 for ltspice-ai-agent.

This module is the stable contract between AI intent and generated LTspice
files. It defines Pydantic models, validation rules, and structured errors
per the LTspice AI Agent Engineering Plan, section 10.

Phase 1 scope:
- Load and validate IR from JSON.
- Round-trip serialize IR back to JSON.
- Reject invalid IR with structured, actionable error codes.
- Export JSON Schema for consumers (other agents, IDE plugins).

Phase 11 scope (additive):
- New component kinds: diode, npn, pnp, nmos, pmos, opamp.
- New topologies: inverting_opamp, noninv_opamp, comparator,
  diode_clipper, halfwave_rectifier, bridge_rectifier,
  transistor_switch.
- The IR carries optional ``models`` (per-kind ``.model`` blocks) and
  ``subcircuits`` (``.subckt ... .ends`` definitions). The netlist
  generator (Phase 2) emits those once, in IR order, before any
  component lines. Path-bearing directives (``.include``/``.lib``)
  remain off the allowlist per plan section 18.1.

Phase 11 does NOT include:
- LLM-based prompt expansion.
- Auto-layout beyond the deterministic per-topology placers in asc.py.
- Promotion of new templates by default (still manual, Phase 9).

Security notes (per plan section 18):
- All raw SPICE directives are rejected by default (allowlist empty).
- Component IDs and IR names are validated against a strict slug pattern to
  prevent path traversal when files are written downstream.
- The model is strict: unknown fields are rejected so typos surface early.
- Subcircuit names are validated against the same SPICE identifier
  pattern as component IDs so they cannot smuggle SPICE syntax through
  the IR.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = "0.1"
"""Only schema version currently accepted by the validator."""

# Topology names allowed. MVP ships with the three passive-only
# topologies; Phase 11 adds seven analog templates (op-amp, comparator,
# diode clipper, half/bridge rectifiers, BJT switch). Extend only
# after layout + simulation coverage is verified per plan section 4
# (Non-Goals) and section 6 (Evidence-Based Decisions).
MVP_TOPOLOGIES: frozenset[str] = frozenset(
    {
        "voltage_divider",
        "rc_lowpass",
        "rc_highpass",
        "inverting_opamp",
        "noninv_opamp",
        "comparator",
        "diode_clipper",
        "halfwave_rectifier",
        "bridge_rectifier",
        "transistor_switch",
    }
)

# Analysis kinds supported by the structured `analysis` block. Raw SPICE
# directive strings are deliberately not accepted here; they go through
# the `directives` field which is currently allowlist-empty.
SUPPORTED_ANALYSIS_KINDS: frozenset[str] = frozenset({"op", "dc", "tran", "ac"})

# Component kind <-> SPICE prefix mapping. The kind is semantic ("resistor")
# while the prefix is the SPICE letter ("R"). Both must agree.
KIND_TO_SPICE_PREFIX: dict[str, str] = {
    "voltage_source": "V",
    "current_source": "I",
    "resistor": "R",
    "capacitor": "C",
    "inductor": "L",
    # Phase 11 additions: semiconductor and subcircuit-call kinds.
    "diode": "D",
    "npn": "Q",
    "pnp": "Q",
    "nmos": "M",
    "pmos": "M",
    "opamp": "X",
}

# Node arity per component kind. Phase 11 added kinds follow the SPICE
# convention:
#   diode:     2 nodes (anode, cathode)
#   npn / pnp: 3 nodes (collector, base, emitter)
#   nmos/pmos: 4 nodes (drain, gate, source, bulk)
#   opamp:     5 nodes (in+, in-, v+, v-, out) — subcircuit invocation
KIND_ARITY: dict[str, int] = {
    "voltage_source": 2,
    "current_source": 2,
    "resistor": 2,
    "capacitor": 2,
    "inductor": 2,
    "diode": 2,
    "npn": 3,
    "pnp": 3,
    "nmos": 4,
    "pmos": 4,
    "opamp": 5,
}

# Patterns for semiconductor model / subcircuit names. The model field
# on diode / BJT / MOSFET is the SPICE model name (e.g. "1N4148",
# "BC547", "2N7000"); the value field on an opamp is the subcircuit
# name (e.g. "UniversalOpamp").
#
# Unlike component IDs (which must start with a letter because they
# appear as SPICE labels that conflict with numeric constants), SPICE
# model names commonly start with digits (e.g. "1N4148"). The pattern
# here accepts a leading letter or digit, but forbids leading symbols
# that would be SPICE syntax (parens, dots, brackets).
SEMICON_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.]*$")

# Pattern for safe node names. We allow letters, digits, underscores, and
# a few common SPICE node characters. Ground must be exactly "0" per plan
# section 10.1 ("Node ground must be exactly `0`"); that is enforced as a
# separate rule, not via this pattern.
NODE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$|^0$")

# Pattern for safe project names (used to build filesystem paths downstream).
# Lowercase, starts with letter, allows digits/underscore/hyphen. Length 1-64.
PROJECT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

# Pattern for safe component and measurement identifiers (SPICE-style).
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

# Pattern for safe probe expressions. Two accepted shapes:
#   V(<node>)           -> voltage at a node
#   I(<component_id>)   -> current through a component
PROBE_PATTERN = re.compile(r"^(V\([^()]+\)|I\([A-Za-z][A-Za-z0-9_]*\))$")

# Allowlist for raw SPICE directives carried in the IR's `directives`
# list. Phase 1 left this empty (rejecting any directive) and documented
# that Phase 2 would populate it with curated safe directives. The set
# here matches the allowlist enforced by the netlist generator in
# ``ltagent.netlist.DIRECTIVE_ALLOWLIST``. Path-bearing directives
# (``.include``, ``.lib``) are deliberately excluded: per plan section
# 18.1 they can pull files from outside the workspace and must be
# path-validated by a higher-level orchestrator before they reach
# either the IR or the netlist.
DIRECTIVE_ALLOWLIST: frozenset[str] = frozenset(
    {
        ".tran",
        ".op",
        ".dc",
        ".ac",
        ".meas",
        ".save",
        ".probe",
        ".print",
        ".options",
        ".global",
        ".param",
        ".ic",
        ".nodeset",
        ".temp",
        ".title",
    }
)

# Value string allowed for sources (must be non-empty).
# We accept any non-empty string because SPICE value syntax is rich
# (SIN, PULSE, PWL, DC, AC, SFFM, etc.). Validation that the string
# parses correctly is a Phase 2 concern.
GROUND_NODE = "0"


class IRError(BaseModel):
    """Structured error returned by validation.

    Stable shape so the CLI layer (Phase 0) can serialize to the JSON
    contract defined in plan section 8.2.
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(description="Stable error code, e.g. MISSING_GROUND")
    path: str = Field(description="JSON pointer style path, e.g. components.0.nodes")
    detail: str = Field(description="Human-readable, agent-friendly description")


class ComponentKind(str, Enum):
    VOLTAGE_SOURCE = "voltage_source"
    CURRENT_SOURCE = "current_source"
    RESISTOR = "resistor"
    CAPACITOR = "capacitor"
    INDUCTOR = "inductor"
    # Phase 11 additions. Semiconductor and subcircuit-call kinds.
    DIODE = "diode"
    NPN = "npn"
    PNP = "pnp"
    NMOS = "nmos"
    PMOS = "pmos"
    OPAMP = "opamp"


class AnalysisKind(str, Enum):
    OP = "op"
    DC = "dc"
    TRAN = "tran"
    AC = "ac"


class Component(BaseModel):
    """One circuit element.

    `kind` is semantic (what it is). `spicePrefix` is the SPICE letter
    (V, I, R, C, L). Both must agree. This redundancy lets humans read
    the IR while keeping a single source of truth for generation.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Unique identifier, e.g. R1, C1, Vin")
    kind: ComponentKind
    spicePrefix: str = Field(description="SPICE letter, must match kind")
    nodes: list[str] = Field(
        min_length=1,
        description="Node terminals in canonical order for the kind",
    )
    value: str | None = Field(
        default=None,
        description=(
            "SPICE value string. Required for sources; for resistor / "
            "capacitor / inductor it is the value (e.g. '1k'). For "
            "diode / npn / pnp / nmos / pmos it is the SPICE model name "
            "(e.g. '1N4148', 'BC547', '2N7000'). For opamp it is the "
            "subcircuit name (e.g. 'UniversalOpamp')."
        ),
    )
    model: str | None = Field(
        default=None,
        description=(
            "Optional separate SPICE model name. When set on diode / "
            "npn / pnp / nmos / pmos, takes precedence over `value` "
            "for the model's model-name lookup. Kept separate so the "
            "value field on a resistor-style element still carries the "
            "physical value (e.g. '10k') without colliding with the "
            "model field on a semiconductor."
        ),
    )
    role: str | None = Field(
        default=None,
        description="Semantic role used by planner and layout, e.g. series_resistor",
    )

    @field_validator("id")
    @classmethod
    def _id_must_match_pattern(cls, v: str) -> str:
        if not IDENTIFIER_PATTERN.match(v):
            raise ValueError(
                f"id {v!r} must match {IDENTIFIER_PATTERN.pattern}"
            )
        return v

    @field_validator("nodes")
    @classmethod
    def _nodes_well_formed(cls, v: list[str]) -> list[str]:
        for n in v:
            if not NODE_NAME_PATTERN.match(n):
                raise ValueError(f"node name {n!r} is not a safe SPICE node")
        return v

    @field_validator("spicePrefix")
    @classmethod
    def _prefix_matches_kind(cls, v: str, info: Any) -> str:
        kind = info.data.get("kind")
        if kind is None:
            return v
        expected = KIND_TO_SPICE_PREFIX.get(kind.value)
        if expected is None:
            raise ValueError(f"unknown component kind: {kind!r}")
        if v != expected:
            raise ValueError(
                f"spicePrefix {v!r} does not match kind {kind.value!r}; "
                f"expected {expected!r}"
            )
        return v

    @model_validator(mode="after")
    def _arity_and_source_value(self) -> Component:
        arity = KIND_ARITY.get(self.kind.value)
        if arity is None:
            raise ValueError(f"unknown component kind: {self.kind!r}")
        if len(self.nodes) != arity:
            raise ValueError(
                f"component {self.id!r} kind {self.kind.value!r} requires "
                f"{arity} nodes, got {len(self.nodes)}"
            )
        if self.kind in (ComponentKind.VOLTAGE_SOURCE, ComponentKind.CURRENT_SOURCE) and (
            not self.value or not self.value.strip()
        ):
            raise ValueError(
                f"source {self.id!r} requires a non-empty value"
            )
        # Phase 11: diode / BJT / MOSFET need a model name (either via
        # `model` or `value`); opamp needs a subcircuit name via `value`.
        semicon_kinds = (
            ComponentKind.DIODE,
            ComponentKind.NPN,
            ComponentKind.PNP,
            ComponentKind.NMOS,
            ComponentKind.PMOS,
        )
        if self.kind in semicon_kinds:
            model_name = self.model or self.value
            if not model_name or not model_name.strip():
                raise ValueError(
                    f"semiconductor {self.id!r} requires a non-empty "
                    "model name in `model` or `value`"
                )
            if not SEMICON_MODEL_PATTERN.match(model_name.strip()):
                raise ValueError(
                    f"semiconductor {self.id!r} model name {model_name!r} "
                    f"must match {SEMICON_MODEL_PATTERN.pattern}"
                )
        if self.kind == ComponentKind.OPAMP:
            if not self.value or not self.value.strip():
                raise ValueError(
                    f"opamp {self.id!r} requires a non-empty subcircuit "
                    "name in `value`"
                )
            if not SEMICON_MODEL_PATTERN.match(self.value.strip()):
                raise ValueError(
                    f"opamp {self.id!r} subcircuit name {self.value!r} "
                    f"must match {SEMICON_MODEL_PATTERN.pattern}"
                )
        return self


class Analysis(BaseModel):
    """Structured simulation analysis.

    Replaces free-form `.tran`, `.op`, `.dc`, `.ac` strings with typed
    fields. Phase 2 will translate these into SPICE directives.
    """

    model_config = ConfigDict(extra="forbid")

    kind: AnalysisKind
    startTime: str | None = Field(
        default=None,
        description="e.g. '0', '1m'. Required for tran.",
    )
    stopTime: str | None = Field(
        default=None,
        description="e.g. '5m'. Required for tran.",
    )
    stepTime: str | None = Field(default=None)
    startFreq: str | None = Field(default=None)
    stopFreq: str | None = Field(default=None)
    pointsPerDecade: int | None = Field(default=None, ge=1)
    sweepVariable: str | None = Field(default=None)
    sweepStart: str | None = Field(default=None)
    sweepStop: str | None = Field(default=None)
    sweepStep: str | None = Field(default=None)

    @model_validator(mode="after")
    def _kind_specific_fields(self) -> Analysis:
        if self.kind == AnalysisKind.TRAN and not self.stopTime:
            raise ValueError("analysis kind 'tran' requires stopTime")
        if self.kind == AnalysisKind.AC and not (self.stopFreq or self.pointsPerDecade):
            raise ValueError(
                "analysis kind 'ac' requires stopFreq or pointsPerDecade"
            )
        if self.kind == AnalysisKind.DC and not self.sweepVariable:
            raise ValueError("analysis kind 'dc' requires sweepVariable")
        return self


class Measurement(BaseModel):
    """A .meas expression to extract after simulation."""

    model_config = ConfigDict(extra="forbid")

    name: str
    analysis: AnalysisKind
    expression: str = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def _safe_name(cls, v: str) -> str:
        if not IDENTIFIER_PATTERN.match(v):
            raise ValueError(
                f"measurement name {v!r} must match {IDENTIFIER_PATTERN.pattern}"
            )
        return v


class Constraints(BaseModel):
    """Optional design targets (targetCutoffHz, targetGain, etc.).

    Keys are free-form (e.g. targetCutoffHz). Values must be flat scalars
    (str, int, float, bool). No nested dicts, no lists. This keeps the
    contract reviewable and prevents accidental injection of complex
    objects.
    """

    model_config = ConfigDict(extra="allow")

    @field_validator("*")
    @classmethod
    def _flat_only(cls, v: Any) -> Any:
        if isinstance(v, (dict, list)):
            raise ValueError(
                "constraints values must be scalars (str, int, float, bool)"
            )
        return v


class Metadata(BaseModel):
    """Optional free-form metadata. Flat only, like Constraints."""

    model_config = ConfigDict(extra="allow")

    @field_validator("*")
    @classmethod
    def _flat_only(cls, v: Any) -> Any:
        if isinstance(v, (dict, list)):
            raise ValueError(
                "metadata values must be scalars (str, int, float, bool)"
            )
        return v


class SemiconductorModel(BaseModel):
    """A SPICE ``.model`` block for a diode / BJT / MOSFET.

    The model is emitted verbatim by the netlist generator (Phase 2)
    as ``.model <name> <type> (<params>)``. The ``name`` and ``type``
    are validated against the SPICE identifier pattern so they cannot
    smuggle SPICE syntax through the IR.

    Examples::

        SemiconductorModel(name="1N4148", type="D", params=("IS=2.55e-9", "RS=0.5"))
        SemiconductorModel(name="BC547", type="NPN", params=("BF=400", "VAF=80"))
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    type: str = Field(min_length=1, description="SPICE model type, e.g. 'D', 'NPN', 'NMOS'")
    params: list[str] = Field(default_factory=list, description="Raw SPICE model parameters")

    @field_validator("name")
    @classmethod
    def _name_matches(cls, v: str) -> str:
        if not SEMICON_MODEL_PATTERN.match(v):
            raise ValueError(
                f"model name {v!r} must match {SEMICON_MODEL_PATTERN.pattern}"
            )
        return v

    @field_validator("type")
    @classmethod
    def _type_matches(cls, v: str) -> str:
        if not SEMICON_MODEL_PATTERN.match(v):
            raise ValueError(
                f"model type {v!r} must match {SEMICON_MODEL_PATTERN.pattern}"
            )
        return v


class Subcircuit(BaseModel):
    """A SPICE ``.subckt ... .ends`` definition for an opamp (or any X-prefixed call).

    The netlist generator emits the block verbatim after all ``.model``
    blocks and before any component lines. The ``name`` becomes the
    first token of the ``.subckt`` line and the positional nodes are
    listed before the inline ``params``. The optional ``body`` field
    is emitted verbatim inside the ``.subckt ... .ends`` block; the
    generator escapes nothing in it because the IR is internal.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    nodes: list[str] = Field(min_length=1)
    params: list[str] = Field(default_factory=list)
    body: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name_matches(cls, v: str) -> str:
        if not SEMICON_MODEL_PATTERN.match(v):
            raise ValueError(
                f"subcircuit name {v!r} must match {SEMICON_MODEL_PATTERN.pattern}"
            )
        return v


class CircuitIR(BaseModel):
    """Top-level Circuit IR v0.1.

    See plan section 10 for the original specification. Validation rules
    in section 10.3 are enforced by Pydantic validators below. Phase 11
    adds the optional ``models`` and ``subcircuits`` fields; older IR
    JSON without these fields round-trips unchanged because both have
    default empty lists.
    """

    model_config = ConfigDict(extra="forbid")

    schemaVersion: str
    name: str
    topology: str
    description: str | None = None
    nodes: list[str]
    components: list[Component] = Field(min_length=1)
    analysis: list[Analysis] = Field(min_length=1)
    measurements: list[Measurement] = Field(default_factory=list)
    probes: list[str] = Field(default_factory=list)
    directives: list[str] = Field(default_factory=list)
    constraints: Constraints | None = None
    models: list[SemiconductorModel] = Field(default_factory=list)
    subcircuits: list[Subcircuit] = Field(default_factory=list)
    metadata: Metadata | None = None

    @field_validator("schemaVersion")
    @classmethod
    def _schema_version_supported(cls, v: str) -> str:
        if v != SCHEMA_VERSION:
            raise ValueError(
                f"schemaVersion {v!r} not supported; expected {SCHEMA_VERSION!r}"
            )
        return v

    @field_validator("name")
    @classmethod
    def _name_is_safe_path(cls, v: str) -> str:
        if not PROJECT_NAME_PATTERN.match(v):
            raise ValueError(
                f"name {v!r} must match {PROJECT_NAME_PATTERN.pattern}"
            )
        return v

    @field_validator("topology")
    @classmethod
    def _topology_supported(cls, v: str) -> str:
        if v not in MVP_TOPOLOGIES:
            raise ValueError(
                f"topology {v!r} not supported in MVP; "
                f"allowed: {sorted(MVP_TOPOLOGIES)}"
            )
        return v

    @field_validator("nodes")
    @classmethod
    def _nodes_well_formed(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("nodes must contain at least one node")
        seen: set[str] = set()
        for n in v:
            if not NODE_NAME_PATTERN.match(n):
                raise ValueError(f"node name {n!r} is not a safe SPICE node")
            if n in seen:
                raise ValueError(f"node {n!r} is duplicated")
            seen.add(n)
        return v

    @field_validator("components")
    @classmethod
    def _component_ids_unique(cls, v: list[Component]) -> list[Component]:
        ids: set[str] = set()
        for c in v:
            if c.id in ids:
                raise ValueError(f"component id {c.id!r} is duplicated")
            ids.add(c.id)
        return v

    @field_validator("probes")
    @classmethod
    def _probe_well_formed(cls, v: list[str]) -> list[str]:
        for p in v:
            if not PROBE_PATTERN.match(p):
                raise ValueError(
                    f"probe {p!r} must be V(<node>) or I(<component_id>)"
                )
        return v

    @field_validator("directives")
    @classmethod
    def _directives_in_allowlist(cls, v: list[str]) -> list[str]:
        # Reject any directive that is not in the curated allowlist. The
        # allowlist is populated in Phase 2 with safe directives; path-
        # bearing directives (``.include``, ``.lib``, ``.model``) are
        # intentionally excluded so they cannot be used to pull files
        # from outside the configured workspace (plan section 18.1).
        for d in v:
            if d.strip() not in DIRECTIVE_ALLOWLIST:
                raise ValueError(
                    f"directive {d!r} is not in allowlist "
                    f"(allowed: {sorted(DIRECTIVE_ALLOWLIST)})"
                )
        return v

    @model_validator(mode="after")
    def _ground_required_and_nodes_consistent(self) -> CircuitIR:
        # 1. Ground node "0" must exist.
        if GROUND_NODE not in self.nodes:
            raise ValueError(
                f"ground node {GROUND_NODE!r} must be present in nodes"
            )
        # 2. Every node referenced by a component must exist.
        node_set = set(self.nodes)
        for comp in self.components:
            for n in comp.nodes:
                if n not in node_set:
                    raise ValueError(
                        f"component {comp.id!r} references unknown node {n!r}"
                    )
        # 3. Every measurement's analysis must exist in this IR's analyses.
        analysis_kinds = {a.kind for a in self.analysis}
        for meas in self.measurements:
            if meas.analysis not in analysis_kinds:
                raise ValueError(
                    f"measurement {meas.name!r} references analysis "
                    f"{meas.analysis.value!r} which is not in analysis list"
                )
        return self


def load_ir(source: str | Path | dict[str, Any]) -> CircuitIR:
    """Load and validate a Circuit IR from a file path, JSON string, or dict.

    Raises:
        FileNotFoundError: if source is a path that does not exist.
        json.JSONDecodeError: if the JSON is malformed.
        pydantic.ValidationError: if validation fails. Callers in higher
            layers should format this with `format_errors`.
    """
    if isinstance(source, dict):
        data = source
    elif isinstance(source, (str, Path)):
        path = Path(source)
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    else:
        raise TypeError(f"unsupported source type: {type(source).__name__}")
    return CircuitIR.model_validate(data)


def dump_ir(ir: CircuitIR, indent: int | None = 2) -> str:
    """Serialize an IR back to JSON string with stable key order."""
    return ir.model_dump_json(indent=indent, by_alias=False)


def format_errors(exc: Exception) -> list[IRError]:
    """Convert a Pydantic ValidationError into structured IRError list.

    The CLI layer (Phase 0) renders this directly into the JSON contract
    defined in plan section 8.2. Codes are stable identifiers that other
    agents can switch on.
    """
    from pydantic import ValidationError  # local import to avoid cycle

    if not isinstance(exc, ValidationError):
        return [
            IRError(
                code="INTERNAL_ERROR",
                path="<root>",
                detail=str(exc),
            )
        ]

    errors: list[IRError] = []
    for err_dict in exc.errors():
        loc = ".".join(str(p) for p in err_dict.get("loc", ())) or "<root>"
        etype = err_dict.get("type", "validation_error")
        msg = err_dict.get("msg", "invalid value")
        ctx = err_dict.get("ctx") or {}
        errors.append(
            IRError(
                code=_pydantic_type_to_code(etype, loc, ctx, err_dict),
                path=loc,
                detail=msg,
            )
        )
    return errors


def _pydantic_type_to_code(
    etype: str, path: str, ctx: dict[str, Any], err_dict: Mapping[str, Any]
) -> str:
    """Map pydantic error type and field path to a stable error code.

    Code namespaces:
      SCHEMA_*      schema-level rejection (version, topology, name)
      NODES_*       node-list validation
      COMP_*        per-component validation
      ANALYSIS_*    analysis block validation
      MEAS_*        measurement validation
      PROBE_*       probe validation
      DIR_*         raw directive rejection
      EXTRA_*       unknown field
      INTERNAL_*    fallback
    """
    head = path.split(".", 1)[0] if path != "<root>" else "<root>"
    table: dict[tuple[str, str], str] = {
        ("schemaVersion", "missing"): "SCHEMA_UNSUPPORTED_VERSION",
        ("schemaVersion", "value_error"): "SCHEMA_UNSUPPORTED_VERSION",
        ("schemaVersion", "string_pattern_mismatch"): "SCHEMA_UNSUPPORTED_VERSION",
        ("schemaVersion", "literal_error"): "SCHEMA_UNSUPPORTED_VERSION",
        ("name", "missing"): "SCHEMA_BAD_NAME",
        ("name", "value_error"): "SCHEMA_BAD_NAME",
        ("name", "string_pattern_mismatch"): "SCHEMA_BAD_NAME",
        ("topology", "missing"): "SCHEMA_BAD_TOPOLOGY",
        ("topology", "value_error"): "SCHEMA_BAD_TOPOLOGY",
        ("topology", "literal_error"): "SCHEMA_BAD_TOPOLOGY",
        ("topology", "enum"): "SCHEMA_BAD_TOPOLOGY",
        ("nodes", "missing"): "NODES_EMPTY",
        ("nodes", "too_short"): "NODES_EMPTY",
        ("nodes", "value_error"): "NODES_DUPLICATE_OR_INVALID",
        ("components", "missing"): "COMP_MISSING",
        ("components", "too_short"): "COMP_MISSING",
        ("directives", "value_error"): "DIR_UNSUPPORTED",
        ("probes", "value_error"): "PROBE_INVALID",
        ("probes", "string_pattern_mismatch"): "PROBE_INVALID",
        ("analysis", "missing"): "ANALYSIS_MISSING",
        ("analysis", "too_short"): "ANALYSIS_MISSING",
    }
    if (head, etype) in table:
        return table[(head, etype)]
    if etype == "missing":
        # Required field missing somewhere.
        leaf = path.split(".")[-1]
        return f"{leaf.upper()}_MISSING" if leaf else "ROOT_MISSING"
    if etype == "extra_forbidden":
        # Path looks like 'sneakyExtraField' or 'components.0.foo'.
        leaf = path.split(".")[-1]
        return f"EXTRA_FIELD_AT_{leaf.upper()}"
    if head == "components" and etype == "value_error":
        # Disambiguate component-internal value errors by matching on the
        # human-readable msg (e.g. "requires 2 nodes", "non-empty value",
        # "does not match kind", "is duplicated").
        msg_lower = str(err_dict.get("msg", "")).lower()
        if "arity" in msg_lower or ("requires" in msg_lower and "nodes" in msg_lower):
            return "COMP_WRONG_ARITY"
        if "non-empty value" in msg_lower:
            return "COMP_SOURCE_VALUE_REQUIRED"
        if "spiceprefix" in msg_lower or "does not match kind" in msg_lower:
            return "COMP_PREFIX_MISMATCH"
        if "duplicated" in msg_lower or "duplicate" in msg_lower:
            return "COMP_DUPLICATE_ID"
        return "COMP_INVALID"
    if head == "analysis":
        return "ANALYSIS_INVALID"
    if head == "measurements":
        return "MEAS_INVALID"
    if head == "<root>":
        msg = str(err_dict.get("msg", "")).lower()
        if "ground" in msg:
            return "NODES_MISSING_GROUND"
        if "unknown node" in msg:
            return "COMP_UNKNOWN_NODE"
        if "measurement" in msg and "analysis" in msg:
            return "MEAS_UNKNOWN_ANALYSIS"
        return "ROOT_INVALID"
    return f"INVALID_{head.upper()}"


def validate_dict(data: dict[str, Any]) -> tuple[CircuitIR | None, list[IRError]]:
    """Validate a dict and return (ir_or_none, errors).

    Convenience helper for callers that already have a parsed dict and
    want both the result and the structured errors without try/except.
    """
    try:
        return CircuitIR.model_validate(data), []
    except Exception as exc:
        from pydantic import ValidationError  # local import

        if isinstance(exc, ValidationError):
            return None, format_errors(exc)
        return None, [
            IRError(
                code="INTERNAL_ERROR",
                path="<root>",
                detail=str(exc),
            )
        ]


__all__ = [
    "DIRECTIVE_ALLOWLIST",
    "GROUND_NODE",
    "IDENTIFIER_PATTERN",
    "KIND_ARITY",
    "KIND_TO_SPICE_PREFIX",
    "MVP_TOPOLOGIES",
    "NODE_NAME_PATTERN",
    "PROBE_PATTERN",
    "PROJECT_NAME_PATTERN",
    "SCHEMA_VERSION",
    "SEMICON_MODEL_PATTERN",
    "SUPPORTED_ANALYSIS_KINDS",
    "Analysis",
    "AnalysisKind",
    "CircuitIR",
    "Component",
    "ComponentKind",
    "Constraints",
    "IRError",
    "Measurement",
    "Metadata",
    "SemiconductorModel",
    "Subcircuit",
    "dump_ir",
    "format_errors",
    "load_ir",
    "validate_dict",
]
