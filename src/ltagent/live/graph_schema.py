"""Pydantic data model for the live-editing Circuit Graph.

The Circuit Graph is the in-memory source of truth for file-based live
editing (see ``ltspice_file_based_live_editing_math_plan.md`` section
7). It sits between the AI's intent and the existing
:class:`ltagent.ir.CircuitIR`: the Edit Operations Agent (Agent 2)
mutates a :class:`CircuitGraph`; Agent 3 (graph -> IR) lowers this
graph into the stable ``CircuitIR`` that the rest of the pipeline
already understands.

The model is intentionally narrow for Phase 1:

* :class:`CircuitGraph`  -- top-level container.
* :class:`Component`     -- one element with a pin map.
* :class:`PinMap`        -- the ``{pin_name: net_name}`` mapping.
* :class:`Net`           -- a named electrical node.
* :class:`Analysis`      -- one simulation analysis block.
* :class:`Measurement`   -- a ``.meas`` expression to extract.
* :class:`Directive`     -- a curated raw SPICE directive.
* :class:`LayoutHint`    -- optional hints for schematic generation.
* :class:`Constraints`   -- optional design targets.

Validation rules referenced here live in
:mod:`ltagent.live.graph_validation`; the schema model itself only
enforces field-level shape (slug patterns, enum membership,
required values) so that the schema can be re-used in other contexts
(snapshot diffing, edit op validation) without re-running the heavier
graph-level checks.

The schema is **not** a re-implementation of :class:`ltagent.ir.CircuitIR`.
The two share identifiers, but the IR adds SPICE-prefix resolution,
model blocks, and the analysis fields LTspice's netlist generator
needs. The graph keeps the model smaller and friendlier to the
LLM-driven edit pipeline.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Only schema version currently emitted by the graph layer. Older
#: versions are rejected at load time. Bump in lockstep with any
#: breaking change to the JSON shape.
SCHEMA_VERSION: str = "0.2"

#: Project-id slug pattern. Mirrors
#: :data:`ltagent.security.SLUG_PATTERN` and the IR's
#: :data:`ltagent.ir.PROJECT_NAME_PATTERN` so projects created via the
#: graph layer can be loaded by the existing CLI without rename.
PROJECT_ID_PATTERN: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

#: SPICE-identifier style. Component ids and measurement names share
#: the same shape (must start with a letter; letters, digits,
#: underscores thereafter). This pattern is the strict one and matches
#: :data:`ltagent.ir.IDENTIFIER_PATTERN`.
IDENTIFIER_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

#: Pin-name pattern. Permissive on purpose: SPICE components use a
#: variety of pin names that do not all match the strict identifier
#: pattern -- two-terminal passives use ``1`` / ``2``, sources use
#: ``+`` / ``-``, op-amp subcircuits use ``in+`` / ``in-`` / ``v+``
#: / ``v-`` / ``out``. The pattern forbids whitespace, slashes, and
#: characters that would be SPICE syntax (parens, dots, brackets,
#: equals) so the pin name cannot smuggle anything through to the
#: generated netlist.
PIN_NAME_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_+\-][A-Za-z0-9_+\-]*$")

#: SPICE-safe net name. Allows leading letter/underscore so node names
#: such as ``out`` or ``fb`` are accepted. The ground net must be
#: exactly ``"0"``; that is enforced as a separate rule.
NODE_NAME_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$|^0$")

#: Ground net name. The graph model requires it to exist (or warns
#: when absent) so downstream generators can rely on a stable
#: reference.
GROUND_NODE: str = "0"

#: Analysis kinds supported by the graph. Mirrors the IR's
#: :data:`ltagent.ir.SUPPORTED_ANALYSIS_KINDS` so the converter does
#: not have to translate enum names.
SUPPORTED_ANALYSIS_KINDS: frozenset[str] = frozenset({"op", "dc", "tran", "ac"})

#: Component kind -> minimum pin arity. ``opamp`` requires 5 pins
#: (in+, in-, v+, v-, out) following the SPICE subcircuit convention
#: used by :class:`ltagent.ir.Component`. ``None`` indicates the
#: component kind has no minimum arity (e.g. for a subcircuit-call
#: placeholder); the schema does not enforce an upper bound.
KIND_MIN_ARITY: dict[str, int] = {
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

#: Component kinds that need a SPICE model name (semiconductor)
#: or a subcircuit name (opamp). Mirrors the rules in
#: :class:`ltagent.ir.Component._arity_and_source_value`.
COMPONENT_KINDS_REQUIRING_MODEL: frozenset[str] = frozenset(
    {
        "diode",
        "npn",
        "pnp",
        "nmos",
        "pmos",
        "opamp",
    }
)

#: Curated allowlist of raw SPICE directives the graph will accept.
#: The list deliberately excludes path-bearing directives
#: (``.include``, ``.lib``) so they cannot be smuggled through the
#: live-editing surface into generated netlists (see plan section
#: 18.1). ``.model`` is also excluded; model definitions belong to
#: the IR, not the graph.
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

#: Domains recognised by the graph layer. The graph itself does not
#: enforce any topology-vs-domain mapping; it just records the hint
#: so the layout + simulation layers can branch on it.
GRAPH_DOMAINS: frozenset[str] = frozenset({"analog", "sensor", "power", "system"})


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ComponentKind(str, Enum):
    """Semantic kind of a circuit element.

    Mirrors :class:`ltagent.ir.ComponentKind` so the graph -> IR
    converter is a 1:1 mapping. New kinds must be added in both
    places at the same time.
    """

    VOLTAGE_SOURCE = "voltage_source"
    CURRENT_SOURCE = "current_source"
    RESISTOR = "resistor"
    CAPACITOR = "capacitor"
    INDUCTOR = "inductor"
    DIODE = "diode"
    NPN = "npn"
    PNP = "pnp"
    NMOS = "nmos"
    PMOS = "pmos"
    OPAMP = "opamp"


class NetType(str, Enum):
    """Electrical role of a net.

    * ``signal``  -- regular signal net (input, output, internal).
    * ``ground``  -- the reference net. At most one such net per
      graph; its name must equal :data:`GROUND_NODE` (i.e. ``"0"``).
    * ``power``   -- supply rail (e.g. ``vcc``, ``vee``). Reserved
      for Phase 11/12 work; accepted here so layout hints can refer
      to supply rails before the graph -> IR converter is taught to
      emit them.
    """

    SIGNAL = "signal"
    GROUND = "ground"
    POWER = "power"


class AnalysisKind(str, Enum):
    """Simulation analysis kind supported by the graph layer."""

    OP = "op"
    DC = "dc"
    TRAN = "tran"
    AC = "ac"


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


class PinMap(BaseModel):
    """Mapping from a component's pin names to net names.

    The graph does not store pin position, length, or any other
    layout data: layout lives in the ``asc`` writer. Pin names are
    SPICE-identifier style (``+``/``-`` for sources, ``1``/``2`` for
    two-terminal passives, ``in+``/``in-``/``out`` for op-amp
    subcircuits). Net names follow :data:`NODE_NAME_PATTERN`.
    """

    model_config = ConfigDict(extra="forbid")

    pins: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Map of pin name to net name. Pin names follow the "
            f"permissive {PIN_NAME_PATTERN.pattern} so SPICE pin "
            "shapes such as ``1``/``2`` (passives), ``+``/``-`` "
            "(sources), and ``in+``/``out`` (op-amp subcircuits) "
            "are all accepted. Net names must match "
            f"{NODE_NAME_PATTERN.pattern}. The map may be empty for "
            "subcircuit placeholders but most concrete kinds need at "
            "least one entry."
        ),
    )

    @field_validator("pins")
    @classmethod
    def _check_pin_and_net_names(cls, v: dict[str, str]) -> dict[str, str]:
        for pin, net in v.items():
            if not isinstance(pin, str) or not PIN_NAME_PATTERN.match(pin):
                raise ValueError(f"pin name {pin!r} must match {PIN_NAME_PATTERN.pattern}")
            if not isinstance(net, str) or not NODE_NAME_PATTERN.match(net):
                raise ValueError(
                    f"net name {net!r} (referenced by pin {pin!r}) is not a safe "
                    f"SPICE node; expected {NODE_NAME_PATTERN.pattern}"
                )
        return v

    def net_for(self, pin: str) -> str | None:
        """Return the net connected to ``pin`` or ``None`` if unconnected."""
        return self.pins.get(pin)

    def nets(self) -> list[str]:
        """Return a deterministic list of unique nets referenced by this map."""
        return sorted(set(self.pins.values()))


class Net(BaseModel):
    """A named electrical net.

    The graph only stores the name and role; connectivity is encoded
    in each :class:`Component`'s :class:`PinMap`. The graph model
    also tolerates a net being declared but not (yet) connected --
    that is reported as a warning by :func:`validate_graph`, not as
    a hard error.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Net name, must match the safe SPICE pattern")
    type: NetType = Field(default=NetType.SIGNAL)
    aliases: list[str] = Field(
        default_factory=list,
        description=(
            "Optional human-friendly aliases used by the schematic "
            "renderer. Each alias must match the safe SPICE pattern."
        ),
    )

    @field_validator("name")
    @classmethod
    def _name_matches(cls, v: str) -> str:
        if not NODE_NAME_PATTERN.match(v):
            raise ValueError(f"net name {v!r} must match {NODE_NAME_PATTERN.pattern}")
        return v

    @field_validator("aliases")
    @classmethod
    def _aliases_well_formed(cls, v: list[str]) -> list[str]:
        for alias in v:
            if not isinstance(alias, str) or not NODE_NAME_PATTERN.match(alias):
                raise ValueError(f"net alias {alias!r} must match {NODE_NAME_PATTERN.pattern}")
        # Aliases are user-facing; keep them deterministic for stable
        # diffs.
        return sorted(set(v))

    @model_validator(mode="after")
    def _ground_must_be_zero(self) -> Net:
        if self.type is NetType.GROUND and self.name != GROUND_NODE:
            raise ValueError(f"ground net must be named {GROUND_NODE!r}, got {self.name!r}")
        return self


class Component(BaseModel):
    """One circuit element.

    The graph component is decoupled from :class:`ltagent.ir.Component`:
    it has no ``spicePrefix`` (the converter derives it from ``kind``)
    and no ``nodes`` list (the pin map is the canonical connectivity
    view). This keeps the model friendly to incremental editing
    where the AI may add pins or change pin ordering without
    confusing the layout generator.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Unique component id, e.g. R1, C1, Vin")
    kind: ComponentKind
    value: str | None = Field(
        default=None,
        description=(
            "SPICE value string. Required for sources; for resistor / "
            "capacitor / inductor it is the value (e.g. '1k'). For "
            "diode / npn / pnp / nmos / pmos it is the SPICE model "
            "name (e.g. '1N4148', 'BC547'). For opamp it is the "
            "subcircuit name (e.g. 'UniversalOpamp')."
        ),
    )
    model: str | None = Field(
        default=None,
        description=(
            "Optional SPICE model name. When set on semiconductor "
            "kinds it takes precedence over ``value``."
        ),
    )
    role: str | None = Field(
        default=None,
        description=(
            "Semantic role used by the layout and topology layers, "
            "e.g. ``series_resistor``, ``shunt_capacitor``."
        ),
    )
    pins: PinMap = Field(
        default_factory=PinMap,
        description="Mapping from pin name to net name.",
    )

    @field_validator("id")
    @classmethod
    def _id_matches(cls, v: str) -> str:
        if not IDENTIFIER_PATTERN.match(v):
            raise ValueError(f"component id {v!r} must match {IDENTIFIER_PATTERN.pattern}")
        return v


class Analysis(BaseModel):
    """Structured simulation analysis.

    Lighter than :class:`ltagent.ir.Analysis`: the graph only needs
    enough to let the converter emit a SPICE directive. Field names
    match the IR's so the converter is a 1:1 mapping.
    """

    model_config = ConfigDict(extra="forbid")

    kind: AnalysisKind
    startTime: str | None = None
    stopTime: str | None = None
    stepTime: str | None = None
    startFreq: str | None = None
    stopFreq: str | None = None
    pointsPerDecade: int | None = Field(default=None, ge=1)
    sweepVariable: str | None = None
    sweepStart: str | None = None
    sweepStop: str | None = None
    sweepStep: str | None = None

    @model_validator(mode="after")
    def _kind_specific_fields(self) -> Analysis:
        if self.kind is AnalysisKind.TRAN and not self.stopTime:
            raise ValueError("analysis kind 'tran' requires stopTime")
        if self.kind is AnalysisKind.AC and not (self.stopFreq or self.pointsPerDecade):
            raise ValueError("analysis kind 'ac' requires stopFreq or pointsPerDecade")
        if self.kind is AnalysisKind.DC and not self.sweepVariable:
            raise ValueError("analysis kind 'dc' requires sweepVariable")
        return self


class Measurement(BaseModel):
    """A ``.meas`` expression to extract after simulation."""

    model_config = ConfigDict(extra="forbid")

    name: str
    analysis: AnalysisKind
    expression: str = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def _safe_name(cls, v: str) -> str:
        if not IDENTIFIER_PATTERN.match(v):
            raise ValueError(f"measurement name {v!r} must match {IDENTIFIER_PATTERN.pattern}")
        return v


class Directive(BaseModel):
    """A curated raw SPICE directive.

    The graph model stores the directive as a structured object
    (rather than a bare list of strings) so the converter can
    distinguish between the directive token and its arguments
    without re-parsing. The token is allowlist-validated.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        description=(
            "The directive token including the leading dot, e.g. "
            "'.tran', '.op'. Must be in DIRECTIVE_ALLOWLIST."
        ),
    )
    args: str = Field(
        default="",
        description=(
            "Free-form argument string after the directive token. "
            "Empty by default; the converter passes the string "
            "through unchanged."
        ),
    )

    @field_validator("name")
    @classmethod
    def _name_allowlisted(cls, v: str) -> str:
        if not isinstance(v, str) or not v.startswith("."):
            raise ValueError(f"directive name {v!r} must start with '.'")
        if v not in DIRECTIVE_ALLOWLIST:
            raise ValueError(
                f"directive {v!r} is not in allowlist (allowed: {sorted(DIRECTIVE_ALLOWLIST)})"
            )
        return v

    def render(self) -> str:
        """Return the directive as a single SPICE-compatible line."""
        if not self.args:
            return self.name
        return f"{self.name} {self.args}".rstrip()


class LayoutHint(BaseModel):
    """Optional hints for schematic generation.

    Hints are non-binding: the deterministic layout in
    :mod:`ltagent.asc` may override them. The graph just records
    what the AI / planner thinks the flow should look like.
    """

    model_config = ConfigDict(extra="forbid")

    flow: str | None = Field(
        default=None,
        description=(
            "Preferred schematic flow, e.g. ``left_to_right``, "
            "``top_to_bottom``. Recognised values are "
            "``left_to_right`` and ``top_to_bottom``; any other "
            "value is treated as ``left_to_right`` by the layout."
        ),
    )
    inputNode: str | None = Field(
        default=None,
        description="Net name expected to be at the schematic's input side.",
    )
    outputNode: str | None = Field(
        default=None,
        description="Net name expected to be at the schematic's output side.",
    )
    anchors: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Optional per-component placement hints. Keys are "
            "component ids; values are free-form anchor names used "
            "by the deterministic layout. Unknown values are "
            "ignored."
        ),
    )

    @field_validator("flow")
    @classmethod
    def _flow_known(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in {"left_to_right", "top_to_bottom"}:
            raise ValueError(
                f"layout flow {v!r} is not recognised; expected 'left_to_right' or 'top_to_bottom'"
            )
        return v

    @field_validator("inputNode", "outputNode")
    @classmethod
    def _node_matches(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not NODE_NAME_PATTERN.match(v):
            raise ValueError(f"layout node {v!r} must match {NODE_NAME_PATTERN.pattern}")
        return v


class Constraints(BaseModel):
    """Optional design targets (targetCutoffHz, targetGain, ...).

    Values must be flat scalars (str / int / float / bool) so the
    JSON shape is reviewable and the Math Core can read it without
    its own schema. This mirrors :class:`ltagent.ir.Constraints`.
    """

    model_config = ConfigDict(extra="allow")

    @field_validator("*")
    @classmethod
    def _flat_only(cls, v: Any) -> Any:
        if isinstance(v, (dict, list)):
            raise ValueError("constraints values must be scalars (str, int, float, bool)")
        return v


# ---------------------------------------------------------------------------
# Top-level container
# ---------------------------------------------------------------------------


class CircuitGraph(BaseModel):
    """Top-level Circuit Graph (live editing source of truth).

    Maps directly to the JSON document described in plan section 7.2.
    The graph is **not** a fully-validated netlist: validation
    happens in :mod:`ltagent.live.graph_validation` so the same
    model can be used to build up partial graphs (e.g. a project
    with only ``Vin`` and a single resistor during incremental
    editing).
    """

    model_config = ConfigDict(extra="forbid")

    schemaVersion: str
    projectId: str
    domain: str = Field(
        default="analog",
        description=(
            "Domain hint. Only ``analog`` is recognised in Phase 1. "
            "Other domains (e.g. ``sensor``) are reserved for later "
            "phases and will be added in lockstep with the topology "
            "library."
        ),
    )
    topology: str = Field(
        default="",
        description=(
            "Optional topology name (e.g. ``rc_lowpass``). Empty "
            "string is allowed while the topology is being assembled."
        ),
    )
    description: str | None = None
    components: dict[str, Component] = Field(
        default_factory=dict,
        description=(
            "Map of component id to component. Keys are the same as "
            "each component's ``id`` field; both are kept for fast "
            "lookup and to keep the JSON shape stable for the AI."
        ),
    )
    nets: dict[str, Net] = Field(
        default_factory=dict,
        description=(
            "Map of net name to net. The ground net, when present, "
            "must be named ``0`` and have ``type == ground``."
        ),
    )
    analyses: list[Analysis] = Field(default_factory=list)
    measurements: list[Measurement] = Field(default_factory=list)
    directives: list[Directive] = Field(default_factory=list)
    constraints: Constraints | None = None
    layoutHints: LayoutHint | None = None

    # ------------------------------------------------------------------
    # Field-level validators
    # ------------------------------------------------------------------

    @field_validator("schemaVersion")
    @classmethod
    def _schema_version_supported(cls, v: str) -> str:
        if v != SCHEMA_VERSION:
            raise ValueError(f"schemaVersion {v!r} not supported; expected {SCHEMA_VERSION!r}")
        return v

    @field_validator("projectId")
    @classmethod
    def _project_id_safe(cls, v: str) -> str:
        if not PROJECT_ID_PATTERN.match(v):
            raise ValueError(f"projectId {v!r} must match {PROJECT_ID_PATTERN.pattern}")
        return v

    @field_validator("domain")
    @classmethod
    def _domain_known(cls, v: str) -> str:
        if v not in GRAPH_DOMAINS:
            raise ValueError(
                f"domain {v!r} is not recognised; expected one of {sorted(GRAPH_DOMAINS)}"
            )
        return v

    @field_validator("components")
    @classmethod
    def _component_keys_match_ids(cls, v: dict[str, Component]) -> dict[str, Component]:
        for key, comp in v.items():
            if key != comp.id:
                raise ValueError(f"components key {key!r} does not match component id {comp.id!r}")
        return v

    @field_validator("nets")
    @classmethod
    def _net_keys_match_names(cls, v: dict[str, Net]) -> dict[str, Net]:
        for key, net in v.items():
            if key != net.name:
                raise ValueError(f"nets key {key!r} does not match net name {net.name!r}")
        return v

    @model_validator(mode="after")
    def _ground_net_consistency(self) -> CircuitGraph:
        """If a ground net is present, its name must be ``0`` and it
        must be unique."""
        ground_nets = [n for n in self.nets.values() if n.type is NetType.GROUND]
        if len(ground_nets) > 1:
            raise ValueError(f"only one ground net is allowed; found {len(ground_nets)}")
        for net in ground_nets:
            if net.name != GROUND_NODE:
                raise ValueError(f"ground net must be named {GROUND_NODE!r}, got {net.name!r}")
        return self


__all__ = [
    "COMPONENT_KINDS_REQUIRING_MODEL",
    "DIRECTIVE_ALLOWLIST",
    "GRAPH_DOMAINS",
    "GROUND_NODE",
    "IDENTIFIER_PATTERN",
    "KIND_MIN_ARITY",
    "NODE_NAME_PATTERN",
    "PIN_NAME_PATTERN",
    "PROJECT_ID_PATTERN",
    "SCHEMA_VERSION",
    "SUPPORTED_ANALYSIS_KINDS",
    "Analysis",
    "AnalysisKind",
    "CircuitGraph",
    "Component",
    "ComponentKind",
    "Constraints",
    "Directive",
    "LayoutHint",
    "Measurement",
    "Net",
    "NetType",
    "PinMap",
]
