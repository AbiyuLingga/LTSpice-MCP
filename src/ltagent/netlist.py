"""Phase 2: Generate ``.cir`` netlists from validated Circuit IR.

This module is the second half of the contract between AI intent and
generated LTspice files. Phase 1 (see ``ltagent.ir``) defines the input.
This module turns it into a syntactically valid LTspice netlist.

Design rules (from ``docs/PROJECT_PLAN.md`` section 11):
- Header comment includes the generator version.
- The IR never carries ``.end``; the netlist generator appends it.
- Analysis directives are emitted from the structured ``analysis`` block,
  not from raw strings.
- Measurement directives are emitted from the structured ``measurements``
  block.
- Raw ``directives`` from the IR are filtered through a strict allowlist;
  anything else raises ``NetlistError``.
- The generator never imports an IR model that is not already validated
  (this module takes a ``CircuitIR`` object, not a raw dict).

Security notes (plan section 18.2):
- The directive allowlist is intentionally narrow in Phase 2:
  ``.tran``, ``.op``, ``.dc``, ``.ac``, ``.meas``, ``.end``, ``.options``,
  ``.save``, ``.probe``, ``.include`` (curated paths only), ``.lib`` (curated
  paths only), ``.global``, ``.param``. ``.include``/``.lib`` paths are
  validated against the project workspace by the caller; this module
  itself only inspects the leading directive name. Path safety is a
  caller concern.
- This module does not execute LTspice, does not touch the filesystem
  beyond the single ``write_text`` call exposed by :func:`write_netlist`,
  and is fully unit-testable without LTspice or Wine.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__
from .ir import (
    Analysis,
    AnalysisKind,
    CircuitIR,
    Component,
    ComponentKind,
    Measurement,
    SemiconductorModel,
    Subcircuit,
)

GENERATOR_NAME = "ltspice-ai-agent"


# --- directive allowlist --------------------------------------------------


DIRECTIVE_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Core analyses. These are normally generated from the structured
        # `analysis` block; the allowlist entry exists so an IR that lists
        # them in `directives` is still round-trippable without a
        # validation error.
        ".tran",
        ".op",
        ".dc",
        ".ac",
        # Measurement / output control. `.meas` is normally generated
        # from `measurements`; the allowlist entry matches the same
        # round-trip concern.
        ".meas",
        ".save",
        ".probe",
        ".print",
        # Simulation control. Path-bearing directives (`.include`, `.lib`,
        # `.model`) are deliberately NOT in this allowlist: per plan
        # section 18.1 they can pull files from outside the workspace
        # and must be path-validated by a higher-level orchestrator
        # before they reach the generator.
        ".options",
        ".global",
        ".param",
        ".ic",
        ".nodeset",
        ".temp",
        ".title",
    }
)
"""SPICE directives the generator is willing to emit as raw lines.

``.include`` and ``.lib`` are intentionally excluded: per plan section
18.1, they can pull files from outside the configured workspace and
must be path-validated by a higher-level orchestrator before they are
passed to the netlist generator. ``.end`` is not in the allowlist
either: the generator appends it itself and the IR is forbidden from
carrying it (plan section 11.2).
"""


MEAS_ALLOWED_ANALYSES: frozenset[str] = frozenset({"op", "dc", "tran", "ac"})
"""Analysis kinds allowed inside ``.meas`` lines. Mirrors
``ltagent.ir.SUPPORTED_ANALYSIS_KINDS``."""


# --- errors --------------------------------------------------------------


class NetlistError(Exception):
    """Raised when a CircuitIR cannot be safely turned into a netlist.

    The error carries a stable ``code`` so the CLI layer can map it to
    the JSON output contract without relying on exception messages.
    """

    def __init__(self, code: str, detail: str, *, data: Mapping[str, Any] | None = None):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.data: dict[str, Any] = dict(data) if data else {}


# --- result types ---------------------------------------------------------


@dataclass(frozen=True)
class NetlistResult:
    """The output of a successful netlist generation.

    Attributes:
        text: Full ``.cir`` body, terminated with a single ``\\n`` and
            ending with ``.end``.
        line_count: Number of non-empty lines in ``text``.
        header: The two-line generator header emitted at the top of the
            netlist. Useful for tests and for ``result.json`` callers.
        component_count: Number of component lines.
        analysis_count: Number of analysis directive lines.
        measurement_count: Number of ``.meas`` directive lines.
        model_count: Number of ``.model`` lines (Phase 11).
        subcircuit_count: Number of ``.subckt ... .ends`` blocks
            (Phase 11). Each block emits one ``.subckt`` line plus one
            ``.ends`` line, so ``subcircuit_block_count`` = ``subcircuit_count``
            and the total directive lines from subcircuits is
            ``2 * subcircuit_count + len(body)``.
        rejected_directives: List of raw-directive strings that the IR
            listed but the generator dropped because they are not in
            ``DIRECTIVE_ALLOWLIST``. Empty in the success path; the
            generator raises ``NetlistError`` instead when safe_mode is
            on (the default), so this is only populated when the caller
            passes ``allow_unknown_directives=True``.
    """

    text: str
    line_count: int
    header: tuple[str, str]
    component_count: int
    analysis_count: int
    measurement_count: int
    model_count: int = 0
    subcircuit_count: int = 0
    rejected_directives: list[str] = field(default_factory=list)


# --- header ---------------------------------------------------------------


def _build_header(ir: CircuitIR) -> tuple[str, ...]:
    """Return the comment lines printed at the top of the netlist.

    The header is intentionally comment-only (``*`` prefixed) so LTspice
    ignores it. We never put user data into the header because the
    project name has already been validated by ``CircuitIR``.
    """
    return (
        f"* Generated by {GENERATOR_NAME} {__version__}",
        f"* Project: {ir.name}",
        f"* Topology: {ir.topology}",
    )


# --- component lines ------------------------------------------------------


def _format_component_line(comp: Component) -> str:
    """Render one SPICE component line.

    Sources need their value string after the two nodes; passive
    components need it after the two nodes as well. We preserve the
    value exactly as authored so SPICE-rich expressions like
    ``SINE(0 1 1k)`` and ``DC 12`` survive untouched.

    Phase 11 added kinds render as follows (per plan section 11):

    * diode:    ``Did anode cathode modelname``
    * npn/pnp:  ``Qid collector base emitter modelname``
    * nmos/pmos:``Mid drain gate source bulk modelname``
    * opamp:    ``Xid in+ in- v+ v- out subcktname``

    The model name is taken from ``comp.model`` when set, otherwise
    from ``comp.value`` (the IR validator guarantees one of them is
    present for semiconductor kinds; the netlist generator never
    decides which is right). Opamp uses ``comp.value`` as the
    subcircuit name.
    """
    nodes = " ".join(comp.nodes)
    value = comp.value or ""
    if comp.kind in (ComponentKind.VOLTAGE_SOURCE, ComponentKind.CURRENT_SOURCE):
        if not value:
            # Defensive: CircuitIR already rejects empty source values,
            # but we never want a malformed line to reach the file.
            raise NetlistError(
                "COMP_SOURCE_VALUE_REQUIRED",
                f"source {comp.id!r} has no value",
                data={"componentId": comp.id},
            )
        return f"{comp.id} {nodes} {value}"

    semicon_kinds = (
        ComponentKind.DIODE,
        ComponentKind.NPN,
        ComponentKind.PNP,
        ComponentKind.NMOS,
        ComponentKind.PMOS,
    )
    if comp.kind in semicon_kinds:
        model_name = (comp.model or comp.value or "").strip()
        if not model_name:
            raise NetlistError(
                "COMP_MODEL_REQUIRED",
                f"semiconductor {comp.id!r} has no model name",
                data={"componentId": comp.id, "kind": comp.kind.value},
            )
        return f"{comp.id} {nodes} {model_name}"

    if comp.kind == ComponentKind.OPAMP:
        subckt_name = value.strip()
        if not subckt_name:
            raise NetlistError(
                "COMP_SUBCKT_REQUIRED",
                f"opamp {comp.id!r} has no subcircuit name",
                data={"componentId": comp.id},
            )
        return f"{comp.id} {nodes} {subckt_name}"

    return f"{comp.id} {nodes} {value}".rstrip()


def _render_components(components: Sequence[Component]) -> list[str]:
    """Render all component lines in IR order.

    IR order is preserved because some downstream consumers (and the
    layout writer in Phase 5) rely on the declared ordering of
    series / shunt elements.
    """
    return [_format_component_line(c) for c in components]


# --- model and subcircuit lines (Phase 11) -------------------------------


def _format_model(model: SemiconductorModel) -> str:
    """Render one ``.model <name> <type> (<params>)`` line.

    Model name and type come from the structured IR; the parameter
    list is emitted verbatim inside parentheses (separated by spaces).
    An empty parameter list emits the closed form ``()`` so the
    directive is unambiguous even with no parameters.
    """
    if model.params:
        joined = " ".join(p.strip() for p in model.params if p.strip())
        return f".model {model.name} {model.type} ({joined})"
    return f".model {model.name} {model.type} ()"


def _format_subcircuit(sub: Subcircuit) -> list[str]:
    """Render one ``.subckt <name> <nodes...> [params]`` ... ``.ends`` block.

    The first emitted line is the ``.subckt`` declaration. Optional
    ``body`` lines are emitted verbatim between the declaration and
    the closing ``.ends``. Per SPICE convention the closing line
    carries the subcircuit name; we preserve that for readability.
    """
    nodes = " ".join(sub.nodes)
    head = f".subckt {sub.name} {nodes}".rstrip()
    if sub.params:
        head = f"{head} {' '.join(p.strip() for p in sub.params if p.strip())}".rstrip()
    lines = [head]
    for line in sub.body:
        lines.append(line.rstrip())
    lines.append(f".ends {sub.name}")
    return lines


def _render_models_and_subcircuits(ir: CircuitIR) -> list[str]:
    """Emit all ``.model`` and ``.subckt`` blocks in IR order.

    Phase 2 never saw these fields; the emitter handles them
    transparently for IRs whose models / subcircuits lists are empty
    (the MVP IRs all qualify). The blocks appear before any component
    lines so a component that references ``X``-prefixed subcircuits
    or ``Q``-prefixed models has those definitions already in scope.
    """
    lines: list[str] = []
    for model in ir.models:
        lines.append(_format_model(model))
    for sub in ir.subcircuits:
        lines.extend(_format_subcircuit(sub))
    return lines


# --- analysis lines -------------------------------------------------------


def _format_tran(a: Analysis) -> str:
    # The IR describes `startTime` as required for `tran` but allows it
    # to default to None. We normalize that to "0" at generation time so
    # the emitted line is always the unambiguous `<start> <stop>` form
    # shown in the plan's reference output (section 11.2). When
    # `stepTime` is provided we emit the three-arg `<step> <start>
    # <stop>` form so users with custom step settings get a literal
    # record of their choice.
    start = a.startTime if a.startTime is not None else "0"
    stop = a.stopTime or ""
    if a.stepTime is not None:
        return f".tran {a.stepTime} {start} {stop}".rstrip()
    return f".tran {start} {stop}".rstrip()


def _format_op(a: Analysis) -> str:
    return ".op"


def _format_dc(a: Analysis) -> str:
    parts = [".dc", a.sweepVariable or ""]
    if a.sweepStart is not None:
        parts.append(a.sweepStart)
    if a.sweepStop is not None:
        parts.append(a.sweepStop)
    if a.sweepStep is not None:
        parts.append(a.sweepStep)
    return " ".join(p for p in parts if p)


def _format_ac(a: Analysis) -> str:
    # Two accepted shapes:
    #   .ac <type> <points> <startFreq> <stopFreq>
    #   .ac <type> <points> <stopFreq>
    parts = [".ac"]
    # LTspice's sweep type defaults to "dec" in many user netlists; we
    # keep that default explicit so the generated netlist is reviewable.
    parts.append("dec")
    if a.pointsPerDecade is not None:
        parts.append(str(a.pointsPerDecade))
    if a.startFreq is not None:
        parts.append(a.startFreq)
    if a.stopFreq is not None:
        parts.append(a.stopFreq)
    return " ".join(p for p in parts if p)


_ANALYSIS_FORMATTERS = {
    AnalysisKind.TRAN: _format_tran,
    AnalysisKind.OP: _format_op,
    AnalysisKind.DC: _format_dc,
    AnalysisKind.AC: _format_ac,
}


def _render_analyses(analyses: Sequence[Analysis]) -> list[str]:
    """Render one directive per analysis entry, in IR order."""
    rendered: list[str] = []
    for a in analyses:
        try:
            fmt = _ANALYSIS_FORMATTERS[a.kind]
        except KeyError as exc:  # pragma: no cover - AnalysisKind enum is closed
            raise NetlistError(
                "ANALYSIS_UNSUPPORTED",
                f"analysis kind {a.kind.value!r} is not supported by the netlist generator",
                data={"kind": a.kind.value},
            ) from exc
        rendered.append(fmt(a))
    return rendered


# --- measurement lines ----------------------------------------------------


def _format_meas(m: Measurement) -> str:
    """Render one ``.meas <analysis> <name> <expression>`` line.

    The ``analysis`` token is the SPICE-style analysis keyword (``tran``,
    ``op``, ``dc``, ``ac``). The ``expression`` is taken verbatim from
    the IR so user-defined ``MAX V(out)`` style expressions survive.
    """
    if m.analysis.value not in MEAS_ALLOWED_ANALYSES:
        raise NetlistError(
            "MEAS_UNKNOWN_ANALYSIS",
            f"measurement {m.name!r} references analysis {m.analysis.value!r} "
            "which the netlist generator does not know how to emit",
            data={"measurement": m.name, "analysis": m.analysis.value},
        )
    return f".meas {m.analysis.value} {m.name} {m.expression}"


def _render_measurements(measurements: Sequence[Measurement]) -> list[str]:
    return [_format_meas(m) for m in measurements]


# --- raw directives -------------------------------------------------------


def _split_directive(directive: str) -> tuple[str, str]:
    """Split a raw SPICE directive into (leading_token, body).

    The leading token is the first whitespace-delimited word, normalized
    to lowercase so ``.TRAN 0 5m`` and ``.tran 0 5m`` match the same
    allowlist entry.

    Examples:
        >>> _split_directive(".TRAN 0 5m")
        ('.tran', '0 5m')
        >>> _split_directive(".include /etc/passwd")
        ('.include', '/etc/passwd')
        >>> _split_directive(".end")
        ('.end', '')
    """
    stripped = directive.strip()
    if not stripped:
        return ("", "")
    head, _, body = stripped.partition(" ")
    return (head.lower(), body.strip())


def _filter_directives(
    directives: Sequence[str],
    *,
    allow_unknown: bool,
) -> tuple[list[str], list[str]]:
    """Split IR raw directives into (kept_lines, rejected_lines).

    Lines are kept if and only if their leading token is in
    ``DIRECTIVE_ALLOWLIST``. Rejected lines are returned separately so
    the caller can either error (default) or downgrade to a warning.
    """
    kept: list[str] = []
    rejected: list[str] = []
    for d in directives:
        if not d.strip():
            continue
        head, _ = _split_directive(d)
        if head in DIRECTIVE_ALLOWLIST:
            kept.append(d.strip())
        else:
            rejected.append(d.strip())
    if rejected and not allow_unknown:
        # Report the first rejected one as the primary error; the data
        # payload carries the full list for the CLI to render.
        first = rejected[0]
        raise NetlistError(
            "DIR_UNSUPPORTED",
            f"raw directive {first!r} is not in the Phase 2 allowlist",
            data={
                "directive": first,
                "rejected": rejected,
                "allowlist": sorted(DIRECTIVE_ALLOWLIST),
            },
        )
    return kept, rejected


# --- public API -----------------------------------------------------------


def render_netlist(
    ir: CircuitIR,
    *,
    allow_unknown_directives: bool = False,
) -> NetlistResult:
    """Render ``ir`` to a :class:`NetlistResult` without touching the filesystem.

    The result's ``text`` attribute is a complete ``.cir`` body: header
    comments, blank-line separators between sections, analysis,
    measurements, and a final ``.end``. The generator never executes
    anything and never imports the IR's raw directives verbatim; raw
    directives are filtered through the allowlist above.

    Args:
        ir: A :class:`CircuitIR` already validated by ``load_ir``.
        allow_unknown_directives: If True, drop raw directives not in
            the allowlist and report them in
            ``NetlistResult.rejected_directives`` instead of raising.
            Defaults to False: unknown directives raise ``NetlistError``.

    Returns:
        :class:`NetlistResult` with the rendered text and counters.
    """
    header = _build_header(ir)
    definitions = _render_models_and_subcircuits(ir)
    component_lines = _render_components(ir.components)
    analysis_lines = _render_analyses(ir.analysis)
    measurement_lines = _render_measurements(ir.measurements)
    kept_directives, rejected = _filter_directives(
        ir.directives,
        allow_unknown=allow_unknown_directives,
    )

    sections: list[str] = []
    sections.append("\n".join(header))
    if definitions:
        sections.append("\n".join(definitions))
    if component_lines:
        sections.append("\n".join(component_lines))
    if analysis_lines:
        sections.append("\n".join(analysis_lines))
    if measurement_lines:
        sections.append("\n".join(measurement_lines))
    if kept_directives:
        sections.append("\n".join(kept_directives))
    sections.append(".end")
    text = "\n".join(sections) + "\n"

    line_count = sum(1 for line in text.splitlines() if line.strip())

    return NetlistResult(
        text=text,
        line_count=line_count,
        header=(header[0], header[1]),
        component_count=len(component_lines),
        analysis_count=len(analysis_lines),
        measurement_count=len(measurement_lines),
        model_count=len(ir.models),
        subcircuit_count=len(ir.subcircuits),
        rejected_directives=rejected,
    )


def write_netlist(
    ir: CircuitIR,
    out_path: Path | str,
    *,
    allow_unknown_directives: bool = False,
    encoding: str = "utf-8",
) -> NetlistResult:
    """Render ``ir`` and write the result to ``out_path``.

    The caller is responsible for path-safety (no traversal, parent
    directory exists, etc.). This function only performs the render
    and the write. It returns the same :class:`NetlistResult` as
    :func:`render_netlist` so the CLI layer can report counters.

    The output file is overwritten if it already exists; the agent
    workflow always treats ``.cir`` as a derived artifact.
    """
    result = render_netlist(
        ir,
        allow_unknown_directives=allow_unknown_directives,
    )
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(result.text, encoding=encoding)
    return result


# --- exports --------------------------------------------------------------


__all__ = [
    "DIRECTIVE_ALLOWLIST",
    "GENERATOR_NAME",
    "MEAS_ALLOWED_ANALYSES",
    "NetlistError",
    "NetlistResult",
    "render_netlist",
    "write_netlist",
]
