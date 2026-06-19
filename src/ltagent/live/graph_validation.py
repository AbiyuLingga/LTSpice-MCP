"""Graph-level validation for the live-editing Circuit Graph.

The schema in :mod:`ltagent.live.graph_schema` enforces field shape
(identifiers, enums, ground net name) but the heavier rules -- the
ones that need to look at the whole graph -- live here. Splitting the
two lets the schema stay cheap to construct during incremental
editing while still producing a fully-structured
:class:`ValidationResult` for the Edit Operations Agent.

Validation rules implemented here (Phase 1 minimum, see plan
section 7.3):

* ``projectId`` is not empty (the schema already enforces the slug
  pattern; this rule is the explicit "non-empty" check that plan
  7.3 asks for).
* Component ids are unique.
* Every component has a non-empty ``kind`` (the schema's enum
  guarantees this; the rule surfaces it as a graph-level finding
  so the result is uniform with the others).
* Every component has at least the minimum number of pins for its
  kind (see :data:`ltagent.live.graph_schema.KIND_MIN_ARITY`).
* Every pin connects to a known net, either declared in ``nets``
  or implicitly referenced. Pins referencing an undeclared net
  trigger an error; pins referencing a net that *is* declared but
  has no other connection trigger a warning (floating net).
* The ground net ``0`` is recognised when present; an empty graph
  emits a "ground missing" warning rather than a hard error so the
  AI can build the graph incrementally.
* Every measurement references an ``analysis`` that exists in the
  graph.
* Every ``.model``-style component (``diode``, BJT, MOSFET,
  opamp) has a model / subcircuit name. This matches the IR's
  :class:`ltagent.ir.Component` rule.
* Raw directives in :data:`ltagent.live.graph_schema.DIRECTIVE_ALLOWLIST`
  are accepted; the schema already rejects anything else.

The result is returned as a :class:`ValidationResult` carrying both
errors (hard failures) and warnings (soft signals). The Edit
Operations Agent and the IR converter decide how to handle each.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .graph_schema import (
    COMPONENT_KINDS_REQUIRING_MODEL,
    DIRECTIVE_ALLOWLIST,
    GROUND_NODE,
    KIND_MIN_ARITY,
    CircuitGraph,
    ComponentKind,
    NetType,
)

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    """Severity of a validation finding."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


# Stable error codes. The CLI / MCP layer can switch on these to
# render user-friendly diagnostics without re-parsing the message.
CODE_PROJECT_ID_EMPTY: str = "GRAPH_PROJECT_ID_EMPTY"
CODE_PROJECT_ID_INVALID: str = "GRAPH_PROJECT_ID_INVALID"
CODE_COMPONENT_ID_DUPLICATE: str = "GRAPH_COMPONENT_ID_DUPLICATE"
CODE_COMPONENT_MISSING_KIND: str = "GRAPH_COMPONENT_MISSING_KIND"
CODE_COMPONENT_MISSING_VALUE: str = "GRAPH_COMPONENT_MISSING_VALUE"
CODE_COMPONENT_MISSING_MODEL: str = "GRAPH_COMPONENT_MISSING_MODEL"
CODE_COMPONENT_INSUFFICIENT_PINS: str = "GRAPH_COMPONENT_INSUFFICIENT_PINS"
CODE_COMPONENT_PIN_UNKNOWN_NET: str = "GRAPH_COMPONENT_PIN_UNKNOWN_NET"
CODE_COMPONENT_PIN_BLANK: str = "GRAPH_COMPONENT_PIN_BLANK"
CODE_GROUND_MISSING: str = "GRAPH_GROUND_MISSING"
CODE_NET_UNKNOWN: str = "GRAPH_NET_UNKNOWN"
CODE_NET_FLOATING: str = "GRAPH_NET_FLOATING"
CODE_MEAS_UNKNOWN_ANALYSIS: str = "GRAPH_MEAS_UNKNOWN_ANALYSIS"
CODE_DIRECTIVE_DISALLOWED: str = "GRAPH_DIRECTIVE_DISALLOWED"
CODE_COMPONENT_INVALID_PIN_NAME: str = "GRAPH_COMPONENT_INVALID_PIN_NAME"
CODE_COMPONENT_INVALID_NET_NAME: str = "GRAPH_COMPONENT_INVALID_NET_NAME"


# Regex used by the validator to flag malformed names without
# raising. The schema has already rejected the same patterns at
# construction time; this regex exists so the validator can
# reproduce a uniform code for malformed values that come from
# ``graph_from_dict`` (which still constructs a graph even if a
# value is questionable -- the schema's strictness depends on
# Pydantic's coercion rules).
_SAFE_IDENTIFIER_RE: re.Pattern[str] = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_SAFE_PIN_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_+\-][A-Za-z0-9_+\-]*$")
_SAFE_NODE_RE: re.Pattern[str] = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$|^0$")
_SAFE_PROJECT_ID_RE: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


# ---------------------------------------------------------------------------
# Issue / result containers
# ---------------------------------------------------------------------------


class ValidationIssue(BaseModel):
    """A single validation finding.

    Pydantic-friendly so the same object can be embedded in the
    JSON contract used by the CLI / MCP layer (plan section 8.2).
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(description="Stable error code, e.g. GRAPH_GROUND_MISSING")
    severity: Severity
    path: str = Field(
        description=(
            "JSON-pointer style path to the offending location, e.g. "
            "``components.R1.pins.1`` or ``nets.out``."
        ),
    )
    detail: str = Field(description="Human-readable, agent-friendly description")
    target: str | None = Field(
        default=None,
        description=(
            "Optional logical target -- component id, net name, or "
            "measurement name -- so callers can filter without "
            "re-parsing the path or detail."
        ),
    )

    def to_dict(self) -> dict[str, str | None]:
        """Return a stable dict shape for JSON serialisation."""
        return {
            "code": self.code,
            "severity": self.severity.value,
            "path": self.path,
            "detail": self.detail,
            "target": self.target,
        }


class ValidationResult(BaseModel):
    """Outcome of :func:`ltagent.live.graph.validate_graph`.

    ``ok`` is True when no error-severity issues were found.
    Warnings and info findings do not flip ``ok`` to False; the
    Edit Operations Agent decides whether to surface them to the
    user. ``errors`` and ``warnings`` are filtered views over
    ``issues``; both are returned sorted by ``path`` for stable
    diffs.
    """

    model_config = ConfigDict(extra="forbid")

    ok: bool = Field(description="True iff no error-severity issues were found")
    issues: list[ValidationIssue] = Field(
        default_factory=list,
        description="All findings, sorted by path then code.",
    )
    errors: list[ValidationIssue] = Field(
        default_factory=list,
        description="Findings with severity == error, sorted by path.",
    )
    warnings: list[ValidationIssue] = Field(
        default_factory=list,
        description="Findings with severity == warning, sorted by path.",
    )

    @property
    def first_error(self) -> ValidationIssue | None:
        """Return the first error or ``None`` when validation passed."""
        return self.errors[0] if self.errors else None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly dict for the CLI / MCP output contract."""
        return {
            "ok": self.ok,
            "issueCount": len(self.issues),
            "errorCount": len(self.errors),
            "warningCount": len(self.warnings),
            "issues": [issue.to_dict() for issue in self.issues],
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_safe_project_id(value: str) -> bool:
    return bool(value) and bool(_SAFE_PROJECT_ID_RE.match(value))


def _is_safe_identifier(value: str) -> bool:
    return bool(_SAFE_IDENTIFIER_RE.match(value))


def _is_safe_pin(value: str) -> bool:
    return bool(_SAFE_PIN_RE.match(value))


def _is_safe_node(value: str) -> bool:
    return bool(_SAFE_NODE_RE.match(value))


def _build_issue(
    code: str,
    severity: Severity,
    path: str,
    detail: str,
    target: str | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        severity=severity,
        path=path,
        detail=detail,
        target=target,
    )


def _check_project_id(graph: CircuitGraph) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not graph.projectId:
        issues.append(
            _build_issue(
                CODE_PROJECT_ID_EMPTY,
                Severity.ERROR,
                path="projectId",
                detail="projectId must be a non-empty string",
            )
        )
    elif not _is_safe_project_id(graph.projectId):
        issues.append(
            _build_issue(
                CODE_PROJECT_ID_INVALID,
                Severity.ERROR,
                path="projectId",
                detail=(
                    f"projectId {graph.projectId!r} must match "
                    f"{_SAFE_PROJECT_ID_RE.pattern}"
                ),
            )
        )
    return issues


def _check_duplicate_component_ids(
    graph: CircuitGraph,
) -> list[ValidationIssue]:
    """Defensive: the schema guarantees uniqueness, but a graph built
    incrementally via ``graph_from_dict`` could carry duplicates if a
    caller bypasses Pydantic. Catch them here so the converter never
    sees a graph with two components sharing an id.
    """
    seen: dict[str, str] = {}
    issues: list[ValidationIssue] = []
    for key, comp in graph.components.items():
        if comp.id != key:
            issues.append(
                _build_issue(
                    CODE_COMPONENT_ID_DUPLICATE,
                    Severity.ERROR,
                    path=f"components.{key}",
                    detail=(
                        f"components key {key!r} does not match "
                        f"component id {comp.id!r}"
                    ),
                    target=comp.id,
                )
            )
            continue
        first_key = seen.setdefault(comp.id, key)
        if first_key != key:
            issues.append(
                _build_issue(
                    CODE_COMPONENT_ID_DUPLICATE,
                    Severity.ERROR,
                    path=f"components.{key}",
                    detail=(
                        f"component id {comp.id!r} is duplicated "
                        f"(first seen at components.{first_key})"
                    ),
                    target=comp.id,
                )
            )
    return issues


def _check_component_kind_and_pins(
    graph: CircuitGraph,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    declared_nets = set(graph.nets.keys())
    referenced_nets: set[str] = set()

    for comp_id, comp in graph.components.items():
        # kind is guaranteed by the schema; surface it as an info-level
        # finding only when the value is empty (defensive: a future
        # schema relaxation could let this happen).
        if not comp.kind:
            issues.append(
                _build_issue(
                    CODE_COMPONENT_MISSING_KIND,
                    Severity.ERROR,
                    path=f"components.{comp_id}.kind",
                    detail=f"component {comp_id!r} is missing a kind",
                    target=comp_id,
                )
            )
            continue

        min_pins = KIND_MIN_ARITY.get(comp.kind.value, 0)
        pin_count = len(comp.pins.pins)
        if min_pins and pin_count < min_pins:
            issues.append(
                _build_issue(
                    CODE_COMPONENT_INSUFFICIENT_PINS,
                    Severity.ERROR,
                    path=f"components.{comp_id}.pins",
                    detail=(
                        f"component {comp_id!r} of kind {comp.kind.value!r} "
                        f"requires at least {min_pins} pins, got {pin_count}"
                    ),
                    target=comp_id,
                )
            )

        # Source-value / model-name rules. The IR enforces these on
        # the converted model; flag them at the graph level so the
        # user gets feedback before the converter runs.
        if comp.kind in (
            ComponentKind.VOLTAGE_SOURCE,
            ComponentKind.CURRENT_SOURCE,
        ) and (not comp.value or not comp.value.strip()):
                issues.append(
                    _build_issue(
                        CODE_COMPONENT_MISSING_VALUE,
                        Severity.ERROR,
                        path=f"components.{comp_id}.value",
                        detail=(
                            f"source component {comp_id!r} requires a "
                            f"non-empty value"
                        ),
                        target=comp_id,
                    )
                )
        if comp.kind.value in COMPONENT_KINDS_REQUIRING_MODEL:
            model_name = comp.model or comp.value
            if not model_name or not model_name.strip():
                issues.append(
                    _build_issue(
                        CODE_COMPONENT_MISSING_MODEL,
                        Severity.ERROR,
                        path=f"components.{comp_id}.model",
                        detail=(
                            f"semiconductor component {comp_id!r} requires a "
                            f"non-empty model or subcircuit name"
                        ),
                        target=comp_id,
                    )
                )

        # Pin / net names. The schema validates these at construction
        # time but a malformed graph can still slip through; flag
        # them here so the report is complete.
        for pin, net in comp.pins.pins.items():
            pin_path = f"components.{comp_id}.pins.{pin}"
            if not pin or not net:
                issues.append(
                    _build_issue(
                        CODE_COMPONENT_PIN_BLANK,
                        Severity.ERROR,
                        path=pin_path,
                        detail=(
                            f"pin entry on {comp_id!r} has a blank pin "
                            f"or net name"
                        ),
                        target=comp_id,
                    )
                )
                continue
            if not _is_safe_pin(pin):
                issues.append(
                    _build_issue(
                        CODE_COMPONENT_INVALID_PIN_NAME,
                        Severity.ERROR,
                        path=pin_path,
                        detail=(
                            f"pin name {pin!r} on {comp_id!r} is not a "
                            f"safe SPICE pin name"
                        ),
                        target=comp_id,
                    )
                )
            if not _is_safe_node(net):
                issues.append(
                    _build_issue(
                        CODE_COMPONENT_INVALID_NET_NAME,
                        Severity.ERROR,
                        path=pin_path,
                        detail=(
                            f"net name {net!r} referenced by pin "
                            f"{pin!r} on {comp_id!r} is not a safe "
                            f"SPICE node"
                        ),
                        target=comp_id,
                    )
                )
                continue
            if net not in declared_nets:
                issues.append(
                    _build_issue(
                        CODE_COMPONENT_PIN_UNKNOWN_NET,
                        Severity.ERROR,
                        path=pin_path,
                        detail=(
                            f"pin {pin!r} on {comp_id!r} references "
                            f"net {net!r} which is not declared in the "
                            f"graph"
                        ),
                        target=comp_id,
                    )
                )
            else:
                referenced_nets.add(net)

    # Floating-net check: a declared net that no component pin
    # references is suspicious. The ground net is exempted because
    # a circuit may not need it (an empty graph, for example).
    floating = sorted(declared_nets - referenced_nets - {GROUND_NODE})
    for name in floating:
        graph_net = graph.nets[name]
        if graph_net.type is NetType.GROUND:
            # An unreferenced ground is just unused; not an error.
            continue
        issues.append(
            _build_issue(
                CODE_NET_FLOATING,
                Severity.WARNING,
                path=f"nets.{name}",
                detail=(
                    f"net {name!r} is declared but no component pin "
                    f"references it"
                ),
                target=name,
            )
        )

    return issues


def _check_ground_presence(
    graph: CircuitGraph,
) -> list[ValidationIssue]:
    """An empty graph (or one without a ground) is allowed but
    emits a warning so the AI / user is reminded to add one before
    generating SPICE."""
    if not graph.nets:
        return [
            _build_issue(
                CODE_GROUND_MISSING,
                Severity.WARNING,
                path="nets",
                detail=(
                    f"graph has no nets declared; a ground net named "
                    f"{GROUND_NODE!r} is required before generating "
                    f"a netlist"
                ),
            )
        ]
    if GROUND_NODE not in graph.nets:
        return [
            _build_issue(
                CODE_GROUND_MISSING,
                Severity.WARNING,
                path="nets",
                detail=(
                    f"ground net {GROUND_NODE!r} is not declared; "
                    f"add it before generating a netlist"
                ),
            )
        ]
    ground = graph.nets[GROUND_NODE]
    if ground.type is not NetType.GROUND:
        return [
            _build_issue(
                CODE_GROUND_MISSING,
                Severity.WARNING,
                path=f"nets.{GROUND_NODE}",
                detail=(
                    f"net {GROUND_NODE!r} exists but its type is "
                    f"{ground.type.value!r}; expected 'ground'"
                ),
            )
        ]
    return []


def _check_measurements(
    graph: CircuitGraph,
) -> list[ValidationIssue]:
    if not graph.measurements:
        return []
    available = {a.kind for a in graph.analyses}
    issues: list[ValidationIssue] = []
    for m in graph.measurements:
        if m.analysis not in available:
            issues.append(
                _build_issue(
                    CODE_MEAS_UNKNOWN_ANALYSIS,
                    Severity.ERROR,
                    path=f"measurements.{m.name}",
                    detail=(
                        f"measurement {m.name!r} references analysis "
                        f"{m.analysis.value!r} which is not declared in "
                        f"the graph's analyses"
                    ),
                    target=m.name,
                )
            )
    return issues


def _check_directives(graph: CircuitGraph) -> list[ValidationIssue]:
    """The schema enforces the allowlist; this is a defensive check
    for callers that build a graph bypassing the schema."""
    issues: list[ValidationIssue] = []
    for i, d in enumerate(graph.directives):
        if d.name not in DIRECTIVE_ALLOWLIST:
            issues.append(
                _build_issue(
                    CODE_DIRECTIVE_DISALLOWED,
                    Severity.ERROR,
                    path=f"directives.{i}",
                    detail=(
                        f"directive {d.name!r} is not in allowlist "
                        f"(allowed: {sorted(DIRECTIVE_ALLOWLIST)})"
                    ),
                )
            )
    return issues


def _collect_issues(graph: CircuitGraph) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    issues.extend(_check_project_id(graph))
    issues.extend(_check_duplicate_component_ids(graph))
    issues.extend(_check_component_kind_and_pins(graph))
    issues.extend(_check_ground_presence(graph))
    issues.extend(_check_measurements(graph))
    issues.extend(_check_directives(graph))
    return issues


def _assemble_result(issues: Iterable[ValidationIssue]) -> ValidationResult:
    issues_list = sorted(issues, key=lambda i: (i.path, i.code))
    errors = [i for i in issues_list if i.severity is Severity.ERROR]
    warnings = [i for i in issues_list if i.severity is Severity.WARNING]
    return ValidationResult(
        ok=not errors,
        issues=issues_list,
        errors=errors,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def validate_graph(graph: CircuitGraph) -> ValidationResult:
    """Run all graph-level checks against ``graph`` and return a
    structured :class:`ValidationResult`.

    The function is pure and deterministic: same input -> same
    result. It never mutates ``graph``. The result is safe to
    serialise through ``ltagent.serialization.to_jsonable``.
    """
    if not isinstance(graph, CircuitGraph):
        raise TypeError(
            f"validate_graph expects CircuitGraph, got {type(graph).__name__}"
        )
    return _assemble_result(_collect_issues(graph))


__all__ = [
    "CODE_COMPONENT_ID_DUPLICATE",
    "CODE_COMPONENT_INSUFFICIENT_PINS",
    "CODE_COMPONENT_INVALID_NET_NAME",
    "CODE_COMPONENT_INVALID_PIN_NAME",
    "CODE_COMPONENT_MISSING_KIND",
    "CODE_COMPONENT_MISSING_MODEL",
    "CODE_COMPONENT_MISSING_VALUE",
    "CODE_COMPONENT_PIN_BLANK",
    "CODE_COMPONENT_PIN_UNKNOWN_NET",
    "CODE_DIRECTIVE_DISALLOWED",
    "CODE_GROUND_MISSING",
    "CODE_MEAS_UNKNOWN_ANALYSIS",
    "CODE_NET_FLOATING",
    "CODE_NET_UNKNOWN",
    "CODE_PROJECT_ID_EMPTY",
    "CODE_PROJECT_ID_INVALID",
    "Severity",
    "ValidationIssue",
    "ValidationResult",
    "validate_graph",
]
