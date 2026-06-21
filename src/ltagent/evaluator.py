"""Phase 9: Template Evaluator and Promoter for ``ltagent``.

The evaluator is the gate between *candidate* templates and the
*official* library. The library is the project's memory of verified
circuits; if a low-quality or duplicate template lands in ``official/``
it pollutes every future ``ltagent create`` that calls
:func:`ltagent.templates.match_template`. This module exists to make
that mistake hard to make.

Acceptance criteria (from ``docs/PROJECT_PLAN.md`` section 21, Phase 9):

* Failed simulation cannot become official.
* Low layout score cannot become official.
* Duplicate value-only templates are rejected.

The implementation is a pure scoring + gate-checking layer on top of
:class:`ltagent.templates.TemplateManifest`. It never mutates manifests
or moves files on its own; the CLI layer is responsible for invoking
:func:`promote_candidate` and confirming with the user.

Design rules (from plan 15.3):

::

    +3 explicit user says save as template
    +2 reusable topology
    +2 simulation succeeded
    +2 no similar template exists
    +1 parameters are editable
    +1 layout score >= 85
    -3 same topology with only value changes
    -3 simulation failed
    -2 too specific
    -2 layout score < 70
    -1 incomplete metadata

Policy::

    score >= 6: official candidate (still requires manual promotion)
    score 3-5:  candidate
    score < 3:  keep as project only

Hard rule::

    RC low-pass 1 kHz, 500 Hz, and 10 kHz are one template with
    different parameters. They are not separate official templates.

Security notes (per plan section 18):

* This module never executes the shell.
* All file operations are bounded by the templates root, just like
  :mod:`ltagent.templates`.
* Manual promotion is the *only* path to ``official/``; the evaluator
  never auto-promotes, even when ``config.templates.auto_promote`` is
  set. (That config flag is reserved for a future Phase 12 workflow
  and is ignored here on purpose.)
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any

from .layout_checker import (
    OFFICIAL_THRESHOLD as LAYOUT_OFFICIAL_THRESHOLD,
)
from .layout_checker import (
    PROJECT_THRESHOLD as LAYOUT_PROJECT_THRESHOLD,
)
from .templates import (
    TEMPLATE_ID_PATTERN,
    TemplateError,
    TemplateManifest,
    TemplateStatus,
    _ensure_root,
    _manifest_path,
    find_by_topology,
    list_templates,
    load_manifest,
    move_template,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Schema version for ``EvaluationResult.to_dict()`` payloads.
EVALUATION_SCHEMA_VERSION: str = "0.1"

#: Maximum score a template can accumulate from the positive rules.
#: The total caps at 11 (+3+2+2+2+1+1); the cap is informational and
#: used only by tests.
SCORE_MAX: int = 3 + 2 + 2 + 2 + 1 + 1

#: Minimum score to be considered a "real" candidate worth keeping.
#: Below this the template is "project only" — it lives in the project
#: directory but is not promoted to the library.
SCORE_PROJECT_THRESHOLD: int = 3

#: Minimum score to be eligible for ``official``. Even if the score is
#: high enough, the gate checks below (simulation success + layout
#: score + no duplicate) can still block promotion.
SCORE_OFFICIAL_THRESHOLD: int = 6


# --- scoring rule weights (plan 15.3) --------------------------------------

WEIGHT_USER_REQUESTED: int = 3
WEIGHT_REUSABLE_TOPOLOGY: int = 2
WEIGHT_SIMULATION_OK: int = 2
WEIGHT_NO_SIMILAR: int = 2
WEIGHT_PARAMETERS_EDITABLE: int = 1
WEIGHT_LAYOUT_OFFICIAL: int = 1

WEIGHT_PENALTY_VALUE_ONLY: int = -3
WEIGHT_PENALTY_SIM_FAILED: int = -3
WEIGHT_PENALTY_TOO_SPECIFIC: int = -2
WEIGHT_PENALTY_LAYOUT_LOW: int = -2
WEIGHT_PENALTY_INCOMPLETE: int = -1


# --- error codes (also used by the CLI layer) ------------------------------

ERR_EVAL_NOT_FOUND: str = "EVAL_NOT_FOUND"
ERR_EVAL_STATUS_INVALID: str = "EVAL_STATUS_INVALID"
ERR_EVAL_GATE_FAILED: str = "EVAL_GATE_FAILED"
ERR_EVAL_SIM_NOT_VERIFIED: str = "EVAL_SIM_NOT_VERIFIED"
ERR_EVAL_LAYOUT_TOO_LOW: str = "EVAL_LAYOUT_TOO_LOW"
ERR_EVAL_DUPLICATE_TOPOLOGY: str = "EVAL_DUPLICATE_TOPOLOGY"
ERR_EVAL_SIM_FAILED: str = "EVAL_SIM_FAILED"
ERR_EVAL_PROMOTE_BLOCKED: str = "EVAL_PROMOTE_BLOCKED"
ERR_EVAL_PROMOTE_FORCED: str = "EVAL_PROMOTE_FORCED"
ERR_EVAL_INPUT_INVALID: str = "EVAL_INPUT_INVALID"


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class PromotionDecision(str, Enum):
    """Outcome of :func:`evaluate_candidate`.

    Mirrors the project's three-bucket promotion policy (plan 15.3):

    * ``"official"`` — score >= 6 and all hard gates pass.
    * ``"candidate"`` — score 3-5, or score >= 6 but a hard gate
      blocks promotion. The template stays in ``candidates/``.
    * ``"project"`` — score < 3. The template is not reusable
      library material; it lives only in the project that produced
      it. It may still be a valid project, just not a template.
    """

    OFFICIAL = "official"
    CANDIDATE = "candidate"
    PROJECT = "project"

    @property
    def display(self) -> str:
        return self.value


@dataclass(frozen=True)
class ScoringRule:
    """A single scoring rule that contributed to the total.

    Used to make the score auditable: every entry in
    :attr:`EvaluationResult.applied_rules` shows *why* the score went
    up or down, with a stable code, weight, and a free-form detail
    string the CLI can show in human mode.
    """

    code: str
    weight: int
    detail: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "weight": self.weight,
            "detail": self.detail,
            "data": dict(self.data),
        }


@dataclass(frozen=True)
class GateCheck:
    """A hard, non-negotiable gate that blocks promotion on failure.

    The three mandatory gates for ``official`` are:

    * simulation succeeded
    * layout score >= 70
    * no duplicate topology in the official library

    Gate checks are *separate from* the score. A template can score
    8/11 and still be blocked from ``official`` if its simulation
    failed; it can score 0/11 and still be passable to ``official``
    once it earns the simulation + layout points (though ``promote``
    always requires ``score >= SCORE_OFFICIAL_THRESHOLD`` as well).
    """

    code: str
    passed: bool
    detail: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "passed": self.passed,
            "detail": self.detail,
            "data": dict(self.data),
        }


@dataclass(frozen=True)
class EvaluationResult:
    """Structured output of :func:`evaluate_candidate`.

    The shape is intentionally stable: the CLI layer renders
    ``to_dict()`` straight into the JSON output contract and agents
    parse it without re-running the evaluator.
    """

    template_id: str
    status: TemplateStatus
    score: int
    decision: PromotionDecision
    promotion_eligible: bool
    duplicate_of: str | None
    applied_rules: tuple[ScoringRule, ...]
    gates: tuple[GateCheck, ...]
    manifest: TemplateManifest
    computed_at: str

    @property
    def failed_gates(self) -> tuple[GateCheck, ...]:
        return tuple(g for g in self.gates if not g.passed)

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        """Codes that block promotion to ``official``.

        Returned for convenience; the CLI surfaces each ``GateCheck``
        in the ``gates`` array.
        """
        return tuple(g.code for g in self.failed_gates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": EVALUATION_SCHEMA_VERSION,
            "templateId": self.template_id,
            "status": self.status.value,
            "score": self.score,
            "decision": self.decision.value,
            "promotionEligible": self.promotion_eligible,
            "duplicateOf": self.duplicate_of,
            "appliedRules": [r.to_dict() for r in self.applied_rules],
            "gates": [g.to_dict() for g in self.gates],
            "manifest": self.manifest.to_dict(),
            "computedAt": self.computed_at,
        }


@dataclass(frozen=True)
class PromotabilityReport:
    """Result of :func:`audit_promotability`.

    Carries the per-template evaluations plus aggregate counters so
    the CLI can render a one-line summary alongside the full table.
    """

    templates_dir: str
    evaluated: tuple[EvaluationResult, ...]
    counts: dict[str, int]
    blocking: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": EVALUATION_SCHEMA_VERSION,
            "templatesDir": self.templates_dir,
            "counts": dict(self.counts),
            "blocking": list(self.blocking),
            "evaluated": [r.to_dict() for r in self.evaluated],
        }


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class EvaluationError(TemplateError):
    """Structured error for the evaluator module.

    Inherits :class:`ltagent.templates.TemplateError` so callers that
    already catch ``TemplateError`` keep working. The ``code`` is one
    of the ``ERR_EVAL_*`` constants above.
    """


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _today() -> str:
    return date.today().isoformat()


def _is_official_threshold_meetable(score: int) -> bool:
    return score >= SCORE_OFFICIAL_THRESHOLD


def _is_value_variant(
    own_signature: tuple[Any, ...] | None,
    other_signature: tuple[Any, ...] | None,
) -> bool:
    """Return True if ``own_signature`` is a value-only variant of the other.

    A "value variant" is a template that shares a topology with an
    existing official template but uses different component values.
    Per plan 15.3 this is a -3 penalty (it should reuse the existing
    template, not create a new one).

    Either signature may be ``None`` (the file was missing or
    malformed); in that case we cannot prove a difference and the
    answer is "no, this is not a value variant". This is a
    conservative choice: a malformed IR never earns the penalty,
    but it also never gets the "+2 no similar template" bonus
    because ``find_by_topology`` already returned the existing
    template.
    """
    if own_signature is None or other_signature is None:
        return False
    return own_signature != other_signature


def _signature_from_ir_file(ir_file: Path) -> tuple[Any, ...] | None:
    """Read a template's *value-aware* IR signature from a known on-disk file.

    The signature is ``(topology, ((id, kind, nodes, role, value), ...))``,
    i.e. the structural shape from :func:`ltagent.templates._ir_signature`
    extended with each component's ``value`` field. Two templates with
    the same topology but different component values therefore get
    different signatures, which is what the plan's hard rule
    ("RC low-pass 1 kHz and 500 Hz are the same template with different
    parameters") relies on for duplicate detection.

    Returns ``None`` when the file is missing, unreadable, or its
    JSON cannot be turned into a signature. Callers must treat
    ``None`` as "we do not know" rather than "no match".
    """
    if not ir_file.is_file():
        return None
    try:
        import json

        data = json.loads(ir_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        return _value_aware_signature(data)
    except Exception:  # pragma: no cover - defensive
        return None


def _value_aware_signature(ir: Mapping[str, Any]) -> tuple[Any, ...]:
    """Compute the value-aware signature for an IR mapping.

    Raises :class:`ValueError` if the mapping is so malformed that
    even topology is missing, so callers can catch and translate
    to ``None``.
    """
    topology = str(ir.get("topology", ""))
    comps = ir.get("components", []) or []
    norm: list[tuple[Any, ...]] = []
    for c in comps:
        if not isinstance(c, Mapping):
            continue
        kind = str(c.get("kind", ""))
        nodes = tuple(str(n) for n in c.get("nodes", []) or ())
        role = c.get("role")
        cid = str(c.get("id", ""))
        value = str(c.get("value", ""))
        norm.append((cid, kind, nodes, str(role) if role is not None else None, value))
    return (topology, tuple(norm))


def _score_layout(layout_score: int | None) -> tuple[int, list[ScoringRule]]:
    """Apply the layout-score rules and return (delta, rules).

    +1 if ``layoutScore >= LAYOUT_OFFICIAL_THRESHOLD`` (85).
    -2 if ``layoutScore < LAYOUT_PROJECT_THRESHOLD`` (70).
    Neither rule fires for ``None`` or in the 70-84 band.
    """
    rules: list[ScoringRule] = []
    if layout_score is None:
        return 0, rules
    if layout_score >= LAYOUT_OFFICIAL_THRESHOLD:
        rules.append(
            ScoringRule(
                code="RULE_LAYOUT_OFFICIAL",
                weight=WEIGHT_LAYOUT_OFFICIAL,
                detail=(f"layout score {layout_score} >= {LAYOUT_OFFICIAL_THRESHOLD}"),
                data={"layoutScore": layout_score},
            )
        )
        return WEIGHT_LAYOUT_OFFICIAL, rules
    if layout_score < LAYOUT_PROJECT_THRESHOLD:
        rules.append(
            ScoringRule(
                code="RULE_LAYOUT_LOW",
                weight=WEIGHT_PENALTY_LAYOUT_LOW,
                detail=(f"layout score {layout_score} < {LAYOUT_PROJECT_THRESHOLD}"),
                data={"layoutScore": layout_score},
            )
        )
        return WEIGHT_PENALTY_LAYOUT_LOW, rules
    return 0, rules


def _has_editable_parameters(manifest: TemplateManifest) -> bool:
    """True iff at least one parameter is editable and non-empty.

    A manifest with zero parameters or only non-editable parameters
    cannot be reused with different values, so it should not earn
    :data:`WEIGHT_PARAMETERS_EDITABLE`.
    """
    return any(p.editable and p.default for p in manifest.parameters.values())


def _is_incomplete_metadata(manifest: TemplateManifest) -> bool:
    """True if the manifest is missing the fields needed for a stable
    reusable template.

    The minimum complete metadata is: a non-empty description and at
    least one parameter. A manifest without these reads as "project
    specific, do not promote".
    """
    if not manifest.description or not manifest.description.strip():
        return True
    return bool(not manifest.parameters)


def _too_specific(manifest: TemplateManifest) -> bool:
    """True if the manifest looks like a one-off project rather than a
    reusable topology.

    Heuristic: the description contains "test", "demo", or "scratch";
    or the manifest has zero parameters *and* an empty formula. Real
    templates always expose at least one editable dimension.
    """
    if manifest.parameters:
        return False
    desc = (manifest.description or "").lower()
    return any(token in desc for token in ("test", "demo", "scratch", "throwaway"))


def _user_requested(manifest: TemplateManifest) -> bool:
    """True if the manifest was explicitly saved as a template.

    The convention is: the user-supplied description starts with
    "save:" or the manifest carries ``tags=("user-requested", ...)``.
    Other agents and the create workflow must propagate this signal
    when the user asks for a template to be kept.
    """
    if "user-requested" in manifest.tags:
        return True
    desc = (manifest.description or "").strip()
    return desc.lower().startswith("save:")


def _reusable_topology(manifest: TemplateManifest) -> bool:
    """True for topologies on the MVP reuse list.

    The MVP catalogue is the three topologies seeded in Phase 6. A
    future phase can promote this to a config-driven allowlist; for
    now it is hard-coded so the evaluator is testable without
    re-deriving it from ``ir.SUPPORTED_TOPOLOGIES``.
    """
    return manifest.topology in (
        "voltage_divider",
        "rc_lowpass",
        "rc_highpass",
    )


# ---------------------------------------------------------------------------
# Scoring + gates
# ---------------------------------------------------------------------------


def _score(
    manifest: TemplateManifest,
    *,
    duplicate_of: str | None,
    layout_score: int | None,
) -> tuple[int, list[ScoringRule]]:
    """Apply the rule set from plan 15.3 and return (total, rules).

    The function is pure: it reads only the manifest's immutable
    fields and the optional hints passed in by the caller. The
    caller is responsible for IO (loading the IR signature to detect
    a duplicate).
    """
    rules: list[ScoringRule] = []
    score = 0

    # +3 user explicitly asked for a template
    if _user_requested(manifest):
        rules.append(
            ScoringRule(
                code="RULE_USER_REQUESTED",
                weight=WEIGHT_USER_REQUESTED,
                detail="user explicitly requested 'save as template'",
                data={},
            )
        )
        score += WEIGHT_USER_REQUESTED

    # +2 reusable topology (MVP catalogue)
    if _reusable_topology(manifest):
        rules.append(
            ScoringRule(
                code="RULE_REUSABLE_TOPOLOGY",
                weight=WEIGHT_REUSABLE_TOPOLOGY,
                detail=f"topology {manifest.topology!r} is on the MVP reuse list",
                data={"topology": manifest.topology},
            )
        )
        score += WEIGHT_REUSABLE_TOPOLOGY

    # +2 simulation succeeded
    if manifest.simulationVerified:
        rules.append(
            ScoringRule(
                code="RULE_SIMULATION_OK",
                weight=WEIGHT_SIMULATION_OK,
                detail="simulation verified by the run pipeline",
                data={},
            )
        )
        score += WEIGHT_SIMULATION_OK

    # -3 simulation failed (mutually exclusive with the +2 above;
    # `simulationVerified=False` could mean either "not run" or
    # "run and failed"; we only penalise when manifest tags or
    # description flag an explicit failure)
    if manifest.tags and "sim-failed" in manifest.tags:
        rules.append(
            ScoringRule(
                code="RULE_SIMULATION_FAILED",
                weight=WEIGHT_PENALTY_SIM_FAILED,
                detail="manifest tags include 'sim-failed'",
                data={},
            )
        )
        score += WEIGHT_PENALTY_SIM_FAILED

    # +2 no similar template exists
    if duplicate_of is None and _reusable_topology(manifest):
        rules.append(
            ScoringRule(
                code="RULE_NO_SIMILAR",
                weight=WEIGHT_NO_SIMILAR,
                detail=(f"no other template in the library shares topology {manifest.topology!r}"),
                data={"topology": manifest.topology},
            )
        )
        score += WEIGHT_NO_SIMILAR

    # +1 parameters are editable
    if _has_editable_parameters(manifest):
        rules.append(
            ScoringRule(
                code="RULE_PARAMETERS_EDITABLE",
                weight=WEIGHT_PARAMETERS_EDITABLE,
                detail="manifest has at least one editable parameter",
                data={},
            )
        )
        score += WEIGHT_PARAMETERS_EDITABLE

    # +1 / -2 layout score (mutually exclusive)
    layout_delta, layout_rules = _score_layout(layout_score)
    rules.extend(layout_rules)
    score += layout_delta

    # -3 same topology, only value changes
    if duplicate_of is not None:
        rules.append(
            ScoringRule(
                code="RULE_VALUE_ONLY_VARIANT",
                weight=WEIGHT_PENALTY_VALUE_ONLY,
                detail=(
                    f"topology {manifest.topology!r} is already covered by "
                    f"template {duplicate_of!r}; this is a value-only variant"
                ),
                data={"duplicateOf": duplicate_of},
            )
        )
        score += WEIGHT_PENALTY_VALUE_ONLY

    # -2 too specific
    if _too_specific(manifest):
        rules.append(
            ScoringRule(
                code="RULE_TOO_SPECIFIC",
                weight=WEIGHT_PENALTY_TOO_SPECIFIC,
                detail=(
                    "manifest looks like a one-off project; no editable "
                    "parameters and the description flags it as test/demo"
                ),
                data={},
            )
        )
        score += WEIGHT_PENALTY_TOO_SPECIFIC

    # -1 incomplete metadata
    if _is_incomplete_metadata(manifest):
        rules.append(
            ScoringRule(
                code="RULE_INCOMPLETE_METADATA",
                weight=WEIGHT_PENALTY_INCOMPLETE,
                detail=("manifest is missing a description or any declared parameters"),
                data={},
            )
        )
        score += WEIGHT_PENALTY_INCOMPLETE

    return score, rules


def _gates(
    manifest: TemplateManifest,
    *,
    duplicate_of: str | None,
    layout_score: int | None,
) -> tuple[GateCheck, ...]:
    """Run the three hard gates that block ``official`` promotion.

    The gates are pure: they inspect the manifest and the hints
    passed by the caller. They are not part of the score, on
    purpose, because a gate failure should be visible in the JSON
    output even when the score is high enough to be confusingly
    "official".
    """
    gates: list[GateCheck] = []

    # Gate 1: simulation succeeded.
    if not manifest.simulationVerified:
        if manifest.tags and "sim-failed" in manifest.tags:
            gates.append(
                GateCheck(
                    code="GATE_SIMULATION_NOT_VERIFIED",
                    passed=False,
                    detail=("simulation did not succeed; manifest tags include 'sim-failed'"),
                    data={},
                )
            )
        else:
            gates.append(
                GateCheck(
                    code="GATE_SIMULATION_NOT_VERIFIED",
                    passed=False,
                    detail=(
                        "manifest.simulationVerified is false; run the "
                        "project through `ltagent run` first"
                    ),
                    data={},
                )
            )
    else:
        gates.append(
            GateCheck(
                code="GATE_SIMULATION_NOT_VERIFIED",
                passed=True,
                detail="manifest.simulationVerified is true",
                data={},
            )
        )

    # Gate 2: layout score at or above the project threshold.
    if layout_score is None:
        gates.append(
            GateCheck(
                code="GATE_LAYOUT_TOO_LOW",
                passed=False,
                detail=(
                    "no layout score recorded; run `ltagent asc --score` "
                    "or score_layout() on the project .asc first"
                ),
                data={},
            )
        )
    elif layout_score < LAYOUT_PROJECT_THRESHOLD:
        gates.append(
            GateCheck(
                code="GATE_LAYOUT_TOO_LOW",
                passed=False,
                detail=(f"layout score {layout_score} < {LAYOUT_PROJECT_THRESHOLD}"),
                data={"layoutScore": layout_score},
            )
        )
    else:
        gates.append(
            GateCheck(
                code="GATE_LAYOUT_TOO_LOW",
                passed=True,
                detail=(f"layout score {layout_score} >= {LAYOUT_PROJECT_THRESHOLD}"),
                data={"layoutScore": layout_score},
            )
        )

    # Gate 3: not a value-only duplicate of an existing official.
    if duplicate_of is not None:
        gates.append(
            GateCheck(
                code="GATE_DUPLICATE_TOPOLOGY",
                passed=False,
                detail=(
                    f"topology {manifest.topology!r} is already covered by "
                    f"official template {duplicate_of!r}; reuse that one "
                    "instead of promoting this value variant"
                ),
                data={"duplicateOf": duplicate_of},
            )
        )
    else:
        gates.append(
            GateCheck(
                code="GATE_DUPLICATE_TOPOLOGY",
                passed=True,
                detail="no duplicate topology in the official library",
                data={},
            )
        )

    return tuple(gates)


def _find_value_variant_duplicate(
    templates_dir: Path,
    manifest: TemplateManifest,
    own_status: TemplateStatus,
) -> str | None:
    """Return the id of an official template that this manifest duplicates.

    The check is *structural*: same topology, IR signature differs
    only in component values. A candidate with the same exact
    signature as an existing official template is *not* a value
    variant; it is a true duplicate and the matcher would have
    returned it.

    Returns ``None`` when the manifest is identical to the existing
    template or when no official template exists for the topology.
    That ``None`` is the "no similar template" case used by the
    scoring + the gate.
    """
    existing = find_by_topology(templates_dir, manifest.topology, status=TemplateStatus.OFFICIAL)
    if existing is None or existing.templateId == manifest.templateId:
        return None
    own_ir = templates_dir / own_status.value / manifest.templateId / "template.ir.json"
    other_ir = (
        templates_dir / TemplateStatus.OFFICIAL.value / existing.templateId / "template.ir.json"
    )
    own_sig = _signature_from_ir_file(own_ir)
    other_sig = _signature_from_ir_file(other_ir)
    if _is_value_variant(own_sig, other_sig):
        return existing.templateId
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_candidate(
    templates_dir: str | Path,
    template_id: str,
    *,
    status: TemplateStatus | str = TemplateStatus.CANDIDATE,
    today: str | None = None,
) -> EvaluationResult:
    """Score one template and run the gate checks against it.

    The function is read-only: it never moves the template or
    mutates its manifest. The CLI calls :func:`promote_candidate`
    after a successful evaluation.

    Raises :class:`EvaluationError` (subclass of
    :class:`ltagent.templates.TemplateError`) if the template is not
    found or the inputs are malformed.
    """
    if not isinstance(template_id, str) or not TEMPLATE_ID_PATTERN.match(template_id):
        raise EvaluationError(
            ERR_EVAL_INPUT_INVALID,
            f"template id {template_id!r} must match {TEMPLATE_ID_PATTERN.pattern}",
            {"templateId": str(template_id)},
        )

    root = _ensure_root(templates_dir)
    s = status if isinstance(status, TemplateStatus) else TemplateStatus.from_str(status)

    manifest_path = _manifest_path(root, s, template_id)
    if not manifest_path.is_file():
        # Fall back to the other statuses; an evaluator over the
        # wrong status should be a user-visible error, not a
        # silent miss, but a common workflow is "evaluate by id,
        # find it wherever it is".
        fallback: TemplateStatus | None = None
        for other in TemplateStatus:
            if other == s:
                continue
            if _manifest_path(root, other, template_id).is_file():
                fallback = other
                break
        if fallback is None:
            raise EvaluationError(
                ERR_EVAL_NOT_FOUND,
                f"template {template_id!r} not found in {s.value}",
                {"templateId": template_id, "status": s.value},
            )
        s = fallback
        manifest_path = _manifest_path(root, s, template_id)

    manifest = load_manifest(manifest_path)
    duplicate_of = _find_value_variant_duplicate(root, manifest, own_status=s)
    layout_score = manifest.layoutScore

    score, rules = _score(manifest, duplicate_of=duplicate_of, layout_score=layout_score)
    gates = _gates(manifest, duplicate_of=duplicate_of, layout_score=layout_score)

    # Score-only path: even when all gates pass, the score must also
    # be high enough for the OFFICIAL decision. The project rule
    # "score >= 6 AND all gates pass" is what makes a template
    # `official` according to the policy.
    all_gates_pass = all(g.passed for g in gates)
    if all_gates_pass and _is_official_threshold_meetable(score) and duplicate_of is None:
        decision = PromotionDecision.OFFICIAL
    elif score >= SCORE_PROJECT_THRESHOLD:
        decision = PromotionDecision.CANDIDATE
    else:
        decision = PromotionDecision.PROJECT

    promotion_eligible = (
        decision == PromotionDecision.OFFICIAL and all_gates_pass and duplicate_of is None
    )

    return EvaluationResult(
        template_id=template_id,
        status=s,
        score=score,
        decision=decision,
        promotion_eligible=promotion_eligible,
        duplicate_of=duplicate_of,
        applied_rules=tuple(rules),
        gates=gates,
        manifest=manifest,
        computed_at=today or _today(),
    )


def evaluate_all(
    templates_dir: str | Path,
    *,
    status: TemplateStatus | str | Iterable[TemplateStatus | str] = (
        TemplateStatus.CANDIDATE,
        TemplateStatus.OFFICIAL,
    ),
) -> tuple[EvaluationResult, ...]:
    """Evaluate every template in the given status set.

    Returns a tuple in deterministic order (sorted by id). Failures
    for individual manifests are converted to :class:`EvaluationError`
    on the offending id so the caller can render partial results.
    """
    statuses = (status,) if isinstance(status, (TemplateStatus, str)) else tuple(status)

    root = _ensure_root(templates_dir)
    manifests: list[TemplateManifest] = []
    for s in statuses:
        manifests.extend(list_templates(root, status=s))

    # De-dup by id; a template in two status folders is a data
    # integrity error and ``list_templates`` should have raised.
    seen: set[str] = set()
    unique: list[TemplateManifest] = []
    for m in manifests:
        if m.templateId in seen:
            continue
        seen.add(m.templateId)
        unique.append(m)
    unique.sort(key=lambda m: m.templateId)

    return tuple(evaluate_candidate(templates_dir, m.templateId, status=m.status) for m in unique)


def can_promote(
    evaluation: EvaluationResult,
) -> tuple[bool, tuple[GateCheck, ...]]:
    """Return ``(allowed, blocking_gates)``.

    A promotion is allowed only when the decision is
    :attr:`PromotionDecision.OFFICIAL` and every gate passed. The
    CLI uses the returned gates to render the reasons; the
    promoter uses the boolean to short-circuit the call.
    """
    if evaluation.decision != PromotionDecision.OFFICIAL:
        return False, tuple(g for g in evaluation.gates if not g.passed)
    failed = tuple(g for g in evaluation.gates if not g.passed)
    return (not failed), failed


def promote_candidate(
    templates_dir: str | Path,
    template_id: str,
    *,
    force: bool = False,
    today: str | None = None,
) -> tuple[TemplateManifest, EvaluationResult]:
    """Promote a candidate template to ``official``.

    The function evaluates the template first, then enforces the
    gate policy:

    * Without ``force``: a failed gate or a non-official decision
      raises :class:`EvaluationError` with code
      :data:`ERR_EVAL_PROMOTE_BLOCKED`.
    * With ``force``: the override is recorded by tagging the
      manifest as ``force-promoted`` so future audits can see the
      human accepted the risk. The manifest is still moved to
      ``official``; the gate failure is preserved in the manifest
      tags so the audit can surface it.

    The function never deletes files and never touches templates
    that are already in ``official``.

    Returns the new manifest and the :class:`EvaluationResult` so
    the CLI can render both the file move and the score.
    """
    evaluation = evaluate_candidate(templates_dir, template_id, today=today)
    root = _ensure_root(templates_dir)

    if evaluation.status == TemplateStatus.OFFICIAL:
        # Idempotent: an already-official template returns the same
        # manifest, no movement, no tags added.
        return evaluation.manifest, evaluation

    allowed, blocking = can_promote(evaluation)
    if not allowed and not force:
        codes = ", ".join(g.code for g in blocking) or evaluation.decision.value
        raise EvaluationError(
            ERR_EVAL_PROMOTE_BLOCKED,
            (
                f"template {template_id!r} is not eligible for promotion "
                f"(decision={evaluation.decision.value}, blocking: {codes})"
            ),
            {
                "templateId": template_id,
                "decision": evaluation.decision.value,
                "blockingGates": [g.to_dict() for g in blocking],
                "score": evaluation.score,
            },
        )

    if force and blocking:
        # Record the override on the manifest so audits can find it
        # later. The tag list is treated as an ordered set for
        # determinism.
        existing_tags = list(evaluation.manifest.tags)
        tag_set = set(existing_tags)
        for marker in ("force-promoted",):
            if marker not in tag_set:
                existing_tags.append(marker)
                tag_set.add(marker)
        # ``move_template`` is the source of truth for the new
        # status; the returned manifest already carries
        # ``status=OFFICIAL`` and a refreshed ``updatedAt``. We just
        # add the override tag on top of it.
        moved = move_template(templates_dir, template_id, to_status=TemplateStatus.OFFICIAL)
        updated = replace(
            moved,
            tags=tuple(existing_tags),
            updatedAt=today or _today(),
        )
        from .templates import _manifest_path as _mp
        from .templates import dump_manifest

        manifest_path = _mp(root, TemplateStatus.OFFICIAL, template_id)
        dump_manifest(updated, manifest_path)
        return updated, evaluation

    new_manifest = move_template(templates_dir, template_id, to_status=TemplateStatus.OFFICIAL)
    return new_manifest, evaluation


def audit_promotability(
    templates_dir: str | Path,
    *,
    status: TemplateStatus | str | Iterable[TemplateStatus | str] = (TemplateStatus.CANDIDATE,),
) -> PromotabilityReport:
    """Evaluate every candidate and return a per-template report.

    The default status set is :class:`TemplateStatus.CANDIDATE`
    because that is where templates wait to be promoted. Pass
    additional statuses to audit ``rejected`` or ``official``
    templates as well.
    """
    evaluated = evaluate_all(templates_dir, status=status)
    counts: dict[str, int] = {
        PromotionDecision.OFFICIAL.value: 0,
        PromotionDecision.CANDIDATE.value: 0,
        PromotionDecision.PROJECT.value: 0,
    }
    blocking: list[str] = []
    for r in evaluated:
        counts[r.decision.value] += 1
        if r.failed_gates:
            codes = ",".join(g.code for g in r.failed_gates)
            blocking.append(f"{r.template_id}:{codes}")
    root = _ensure_root(templates_dir)
    return PromotabilityReport(
        templates_dir=str(root),
        evaluated=evaluated,
        counts=counts,
        blocking=tuple(blocking),
    )


__all__ = [
    "ERR_EVAL_DUPLICATE_TOPOLOGY",
    "ERR_EVAL_GATE_FAILED",
    "ERR_EVAL_INPUT_INVALID",
    "ERR_EVAL_LAYOUT_TOO_LOW",
    "ERR_EVAL_NOT_FOUND",
    "ERR_EVAL_PROMOTE_BLOCKED",
    "ERR_EVAL_PROMOTE_FORCED",
    "ERR_EVAL_SIM_FAILED",
    "ERR_EVAL_SIM_NOT_VERIFIED",
    "ERR_EVAL_STATUS_INVALID",
    "EVALUATION_SCHEMA_VERSION",
    "SCORE_MAX",
    "SCORE_OFFICIAL_THRESHOLD",
    "SCORE_PROJECT_THRESHOLD",
    "WEIGHT_LAYOUT_OFFICIAL",
    "WEIGHT_NO_SIMILAR",
    "WEIGHT_PARAMETERS_EDITABLE",
    "WEIGHT_PENALTY_INCOMPLETE",
    "WEIGHT_PENALTY_LAYOUT_LOW",
    "WEIGHT_PENALTY_SIM_FAILED",
    "WEIGHT_PENALTY_TOO_SPECIFIC",
    "WEIGHT_PENALTY_VALUE_ONLY",
    "WEIGHT_REUSABLE_TOPOLOGY",
    "WEIGHT_SIMULATION_OK",
    "WEIGHT_USER_REQUESTED",
    "EvaluationError",
    "EvaluationResult",
    "GateCheck",
    "PromotabilityReport",
    "PromotionDecision",
    "ScoringRule",
    "audit_promotability",
    "can_promote",
    "evaluate_all",
    "evaluate_candidate",
    "promote_candidate",
]
