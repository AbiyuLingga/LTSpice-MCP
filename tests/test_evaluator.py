"""Unit tests for ``ltagent.evaluator`` (Phase 9).

The tests cover the acceptance criteria from the project plan:

* Failed simulation cannot become official.
* Low layout score cannot become official.
* Duplicate value-only templates are rejected.
* The score and gates match the rule table in plan 15.3.
* Manual promotion is the only path to ``official/``.
* Force-promotion is recorded in the manifest so audits can see it.

All tests are offline. No LTspice / Wine involvement.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from ltagent.evaluator import (
    ERR_EVAL_INPUT_INVALID,
    ERR_EVAL_NOT_FOUND,
    ERR_EVAL_PROMOTE_BLOCKED,
    EVALUATION_SCHEMA_VERSION,
    SCORE_OFFICIAL_THRESHOLD,
    SCORE_PROJECT_THRESHOLD,
    WEIGHT_LAYOUT_OFFICIAL,
    WEIGHT_NO_SIMILAR,
    WEIGHT_PARAMETERS_EDITABLE,
    WEIGHT_PENALTY_LAYOUT_LOW,
    WEIGHT_PENALTY_SIM_FAILED,
    WEIGHT_PENALTY_VALUE_ONLY,
    WEIGHT_REUSABLE_TOPOLOGY,
    WEIGHT_SIMULATION_OK,
    WEIGHT_USER_REQUESTED,
    EvaluationError,
    PromotabilityReport,
    PromotionDecision,
    ScoringRule,
    audit_promotability,
    can_promote,
    evaluate_all,
    evaluate_candidate,
    promote_candidate,
)
from ltagent.templates import (
    TEMPLATE_ID_PATTERN,
    TEMPLATES_SCHEMA_VERSION,
    TemplateManifest,
    TemplateParameter,
    TemplateStatus,
    create_candidate_from_ir,
    list_templates,
    load_manifest,
    seed_default_templates,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def templates_root(tmp_path: Path) -> Iterator[Path]:
    """A clean ``templates/`` directory under tmp_path."""
    root = tmp_path / "templates"
    root.mkdir()
    yield root


@pytest.fixture()
def seeded_templates(templates_root: Path) -> Iterator[Path]:
    """The three MVP official templates installed in a fresh root."""
    seed_default_templates(templates_root)
    yield templates_root


def _rc_ir(topology: str = "rc_lowpass", r1_value: str = "1.59k", c1_value: str = "100n") -> dict:
    """A simple RC low-pass / high-pass IR with configurable values.

    The ``name`` field must satisfy the IR's strict slug pattern
    (``PROJECT_NAME_PATTERN`` = ``^[a-z][a-z0-9_-]{0,63}$``) so it
    cannot contain ``.``. The helper builds a clean name from the
    caller-supplied values when needed.
    """
    def _clean(v: str) -> str:
        return v.replace(".", "p")

    if topology == "rc_lowpass":
        return {
            "schemaVersion": "0.1",
            "name": f"rc_lowpass_{_clean(r1_value)}_{_clean(c1_value)}",
            "topology": topology,
            "description": "First-order RC low-pass filter.",
            "nodes": ["in", "out", "0"],
            "components": [
                {
                    "id": "Vin",
                    "kind": "voltage_source",
                    "spicePrefix": "V",
                    "nodes": ["in", "0"],
                    "value": "SINE(0 1 1k)",
                    "role": "input_source",
                },
                {
                    "id": "R1",
                    "kind": "resistor",
                    "spicePrefix": "R",
                    "nodes": ["in", "out"],
                    "value": r1_value,
                    "role": "series_resistor",
                },
                {
                    "id": "C1",
                    "kind": "capacitor",
                    "spicePrefix": "C",
                    "nodes": ["out", "0"],
                    "value": c1_value,
                    "role": "shunt_capacitor",
                },
            ],
            "analysis": [{"kind": "tran", "stopTime": "5m"}],
            "measurements": [
                {"name": "VOUT_MAX", "analysis": "tran", "expression": "MAX V(out)"},
            ],
            "probes": ["V(in)", "V(out)"],
        }
    if topology == "rc_highpass":
        return {
            "schemaVersion": "0.1",
            "name": f"rc_highpass_{_clean(r1_value)}_{_clean(c1_value)}",
            "topology": topology,
            "description": "First-order RC high-pass filter.",
            "nodes": ["in", "out", "0"],
            "components": [
                {
                    "id": "Vin",
                    "kind": "voltage_source",
                    "spicePrefix": "V",
                    "nodes": ["in", "0"],
                    "value": "SINE(0 1 200)",
                    "role": "input_source",
                },
                {
                    "id": "C1",
                    "kind": "capacitor",
                    "spicePrefix": "C",
                    "nodes": ["in", "out"],
                    "value": c1_value,
                    "role": "series_capacitor",
                },
                {
                    "id": "R1",
                    "kind": "resistor",
                    "spicePrefix": "R",
                    "nodes": ["out", "0"],
                    "value": r1_value,
                    "role": "shunt_resistor",
                },
            ],
            "analysis": [{"kind": "tran", "stopTime": "20m"}],
            "measurements": [
                {"name": "VOUT_MAX", "analysis": "tran", "expression": "MAX V(out)"},
            ],
            "probes": ["V(in)", "V(out)"],
        }
    if topology == "voltage_divider":
        return {
            "schemaVersion": "0.1",
            "name": f"voltage_divider_{_clean(r1_value)}_{_clean(c1_value)}",
            "topology": topology,
            "description": "Resistive voltage divider.",
            "nodes": ["in", "out", "0"],
            "components": [
                {
                    "id": "Vin",
                    "kind": "voltage_source",
                    "spicePrefix": "V",
                    "nodes": ["in", "0"],
                    "value": "DC 12",
                    "role": "input_source",
                },
                {
                    "id": "R1",
                    "kind": "resistor",
                    "spicePrefix": "R",
                    "nodes": ["in", "out"],
                    "value": r1_value,
                    "role": "series_resistor",
                },
                {
                    "id": "R2",
                    "kind": "resistor",
                    "spicePrefix": "R",
                    "nodes": ["out", "0"],
                    "value": c1_value,
                    "role": "shunt_resistor",
                },
            ],
            "analysis": [{"kind": "op"}],
            "measurements": [
                {"name": "VOUT", "analysis": "op", "expression": "V(out)"},
            ],
            "probes": ["V(in)", "V(out)"],
        }
    raise ValueError(topology)


# ---------------------------------------------------------------------------
# Constants / module surface
# ---------------------------------------------------------------------------


def test_evaluation_schema_version_is_exported() -> None:
    assert EVALUATION_SCHEMA_VERSION == "0.1"


def test_score_thresholds_match_plan() -> None:
    # Plan 15.3 hardcodes: score >= 6 official, 3-5 candidate, < 3 project.
    assert SCORE_OFFICIAL_THRESHOLD == 6
    assert SCORE_PROJECT_THRESHOLD == 3


def test_weights_match_plan() -> None:
    # Plan 15.3 scoring rule weights; locked in to keep the policy auditable.
    assert WEIGHT_USER_REQUESTED == 3
    assert WEIGHT_REUSABLE_TOPOLOGY == 2
    assert WEIGHT_SIMULATION_OK == 2
    assert WEIGHT_NO_SIMILAR == 2
    assert WEIGHT_PARAMETERS_EDITABLE == 1
    assert WEIGHT_LAYOUT_OFFICIAL == 1
    assert WEIGHT_PENALTY_VALUE_ONLY == -3
    assert WEIGHT_PENALTY_SIM_FAILED == -3
    assert WEIGHT_PENALTY_LAYOUT_LOW == -2


# ---------------------------------------------------------------------------
# evaluate_candidate — basic shape
# ---------------------------------------------------------------------------


def test_evaluate_seeded_official_template_is_official(seeded_templates: Path) -> None:
    """The seeded ``rc_lowpass`` template should already be a strong official."""
    ev = evaluate_candidate(seeded_templates, "rc_lowpass", status=TemplateStatus.OFFICIAL)
    assert ev.status == TemplateStatus.OFFICIAL
    assert ev.decision == PromotionDecision.OFFICIAL
    assert ev.promotion_eligible is True
    assert ev.duplicate_of is None
    assert ev.score >= SCORE_OFFICIAL_THRESHOLD
    # The three hard gates must all pass.
    assert all(g.passed for g in ev.gates)


def test_evaluate_rejects_invalid_template_id(seeded_templates: Path) -> None:
    with pytest.raises(EvaluationError) as exc:
        evaluate_candidate(seeded_templates, "../etc/passwd")
    assert exc.value.code == ERR_EVAL_INPUT_INVALID


def test_evaluate_unknown_template_raises(seeded_templates: Path) -> None:
    with pytest.raises(EvaluationError) as exc:
        evaluate_candidate(seeded_templates, "does_not_exist")
    assert exc.value.code == ERR_EVAL_NOT_FOUND


def test_evaluate_falls_back_to_other_status(seeded_templates: Path) -> None:
    """If the requested status is wrong, the evaluator finds the template elsewhere."""
    ev = evaluate_candidate(
        seeded_templates, "rc_lowpass", status=TemplateStatus.CANDIDATE
    )
    assert ev.status == TemplateStatus.OFFICIAL  # found the actual location


# ---------------------------------------------------------------------------
# Scoring rules
# ---------------------------------------------------------------------------


def _codes(rules: tuple[ScoringRule, ...]) -> set[str]:
    return {r.code for r in rules}


def test_score_breakdown_for_healthy_official(seeded_templates: Path) -> None:
    ev = evaluate_candidate(seeded_templates, "voltage_divider", status=TemplateStatus.OFFICIAL)
    codes = _codes(ev.applied_rules)
    # The seeded voltage_divider has simulationVerified=True, layoutScore=90,
    # editable parameters, and is on the MVP reuse list.
    assert "RULE_REUSABLE_TOPOLOGY" in codes
    assert "RULE_SIMULATION_OK" in codes
    assert "RULE_NO_SIMILAR" in codes
    assert "RULE_PARAMETERS_EDITABLE" in codes
    assert "RULE_LAYOUT_OFFICIAL" in codes
    # And none of the negative rules should fire.
    for negative in (
        "RULE_LAYOUT_LOW",
        "RULE_VALUE_ONLY_VARIANT",
        "RULE_TOO_SPECIFIC",
        "RULE_INCOMPLETE_METADATA",
        "RULE_SIMULATION_FAILED",
    ):
        assert negative not in codes


def test_user_requested_tag_yields_plus_three(seeded_templates: Path) -> None:
    """A candidate tagged user-requested earns the +3 weight."""
    ir = _rc_ir(r1_value="2.2k", c1_value="100n")
    m = create_candidate_from_ir(
        seeded_templates,
        ir,
        template_id="rc_lowpass_2k2",
        description="save: 2.2k variant",  # signals "user wants this kept"
        layout_score=92,
        simulation_verified=True,
    )
    assert "RULE_USER_REQUESTED" in _codes(
        evaluate_candidate(
            seeded_templates, m.templateId, status=TemplateStatus.CANDIDATE
        ).applied_rules
    )


def test_sim_failed_tag_triggers_penalty_and_gate(seeded_templates: Path) -> None:
    """A manifest tagged sim-failed fails the simulation gate and earns -3."""
    ir = _rc_ir(r1_value="1.5k", c1_value="120n")
    m = create_candidate_from_ir(
        seeded_templates,
        ir,
        template_id="rc_lowpass_failed",
        layout_score=92,
        simulation_verified=False,
    )
    # Manually set the sim-failed tag to simulate a failed run
    # (the create helper does not expose this; the test reaches into
    # the manifest to make the situation realistic).
    from dataclasses import replace

    updated = replace(m, tags=("sim-failed",))
    from ltagent.templates import _manifest_path, dump_manifest

    dump_manifest(updated, _manifest_path(seeded_templates, TemplateStatus.CANDIDATE, m.templateId))
    ev = evaluate_candidate(seeded_templates, m.templateId)
    assert "RULE_SIMULATION_FAILED" in _codes(ev.applied_rules)
    sim_gate = next(g for g in ev.gates if g.code == "GATE_SIMULATION_NOT_VERIFIED")
    assert not sim_gate.passed


def test_low_layout_score_triggers_penalty_and_gate(seeded_templates: Path) -> None:
    ir = _rc_ir(r1_value="1.6k", c1_value="100n")
    m = create_candidate_from_ir(
        seeded_templates,
        ir,
        template_id="rc_lowpass_ugly",
        layout_score=55,
        simulation_verified=True,
    )
    ev = evaluate_candidate(seeded_templates, m.templateId)
    codes = _codes(ev.applied_rules)
    assert "RULE_LAYOUT_LOW" in codes
    layout_gate = next(g for g in ev.gates if g.code == "GATE_LAYOUT_TOO_LOW")
    assert not layout_gate.passed


def test_high_layout_score_yields_plus_one(seeded_templates: Path) -> None:
    ir = _rc_ir(r1_value="1.8k", c1_value="100n")
    m = create_candidate_from_ir(
        seeded_templates,
        ir,
        template_id="rc_lowpass_pretty",
        layout_score=92,
        simulation_verified=True,
    )
    ev = evaluate_candidate(seeded_templates, m.templateId)
    rules_by_code = {r.code: r for r in ev.applied_rules}
    assert rules_by_code["RULE_LAYOUT_OFFICIAL"].weight == 1


def test_layout_score_in_project_band_neutral(seeded_templates: Path) -> None:
    """70 <= score < 85 yields neither the +1 nor the -2."""
    ir = _rc_ir(r1_value="1.7k", c1_value="100n")
    m = create_candidate_from_ir(
        seeded_templates,
        ir,
        template_id="rc_lowpass_ok",
        layout_score=78,
        simulation_verified=True,
    )
    ev = evaluate_candidate(seeded_templates, m.templateId)
    codes = _codes(ev.applied_rules)
    assert "RULE_LAYOUT_OFFICIAL" not in codes
    assert "RULE_LAYOUT_LOW" not in codes
    # But the gate still passes.
    layout_gate = next(g for g in ev.gates if g.code == "GATE_LAYOUT_TOO_LOW")
    assert layout_gate.passed


def test_too_specific_template_triggers_penalty(seeded_templates: Path) -> None:
    """A description like 'demo' and zero parameters is too specific.

    Use a brand-new templates root so the duplicate gate does not
    interfere with the too-specific rule (the only rule we want to
    assert on here).
    """
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "templates"
        root.mkdir()
        data = {
            "schemaVersion": "0.1",
            "name": "test_demo_oneoff",
            "topology": "voltage_divider",
            "description": "irrelevant: only the manifest description matters",
            "nodes": ["in", "out", "0"],
            "components": [
                {"id": "Vin", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["in", "0"], "value": "DC 5", "role": "input_source"},
                {"id": "R1", "kind": "resistor", "spicePrefix": "R",
                 "nodes": ["in", "out"], "value": "1k", "role": "series_resistor"},
                {"id": "R2", "kind": "resistor", "spicePrefix": "R",
                 "nodes": ["out", "0"], "value": "1k", "role": "shunt_resistor"},
            ],
            "analysis": [{"kind": "op"}],
        }
        create_candidate_from_ir(
            root,
            data,
            template_id="test_demo_oneoff",
            description="demo: throwaway scratch divider",  # signals too-specific
            layout_score=92,
            simulation_verified=True,
        )
        ev = evaluate_candidate(root, "test_demo_oneoff")
        assert "RULE_TOO_SPECIFIC" in _codes(ev.applied_rules)


def test_incomplete_metadata_triggers_penalty(seeded_templates: Path) -> None:
    """Missing description or no parameters -> incomplete."""
    data = {
        "schemaVersion": "0.1",
        "name": "no_metadata",
        "topology": "voltage_divider",
        "description": "",  # empty description
        "nodes": ["in", "out", "0"],
        "components": [
            {"id": "Vin", "kind": "voltage_source", "spicePrefix": "V",
             "nodes": ["in", "0"], "value": "DC 5", "role": "input_source"},
            {"id": "R1", "kind": "resistor", "spicePrefix": "R",
             "nodes": ["in", "out"], "value": "1k", "role": "series_resistor"},
            {"id": "R2", "kind": "resistor", "spicePrefix": "R",
             "nodes": ["out", "0"], "value": "1k", "role": "shunt_resistor"},
        ],
        "analysis": [{"kind": "op"}],
    }
    create_candidate_from_ir(
        seeded_templates,
        data,
        template_id="no_metadata",
        layout_score=92,
        simulation_verified=True,
    )
    ev = evaluate_candidate(seeded_templates, "no_metadata")
    assert "RULE_INCOMPLETE_METADATA" in _codes(ev.applied_rules)


def test_no_editable_parameters_no_bonus(seeded_templates: Path) -> None:
    """A manifest with only non-editable parameters should not earn the +1."""
    from dataclasses import replace as dc_replace

    ir = _rc_ir(r1_value="3.3k", c1_value="100n")
    m = create_candidate_from_ir(
        seeded_templates,
        ir,
        template_id="rc_no_edit",
        layout_score=92,
        simulation_verified=True,
    )
    # Force the manifest's parameters to be non-editable.
    updated_params = {
        k: TemplateParameter(description=v.description, default=v.default, editable=False)
        for k, v in m.parameters.items()
    }
    from ltagent.templates import _manifest_path, dump_manifest

    updated = dc_replace(m, parameters=updated_params)
    dump_manifest(updated, _manifest_path(seeded_templates, TemplateStatus.CANDIDATE, m.templateId))
    ev = evaluate_candidate(seeded_templates, m.templateId)
    codes = _codes(ev.applied_rules)
    assert "RULE_PARAMETERS_EDITABLE" not in codes


# ---------------------------------------------------------------------------
# Hard rule: value-only variants
# ---------------------------------------------------------------------------


def test_value_variant_detected_and_penalised(seeded_templates: Path) -> None:
    """RC lowpass with a different R1 should be detected as a value variant of
    the existing official rc_lowpass and earn the -3 penalty plus the duplicate
    gate failure (plan 15.3 hard rule)."""
    ir = _rc_ir(r1_value="2k", c1_value="100n")  # different R1
    m = create_candidate_from_ir(
        seeded_templates,
        ir,
        template_id="rc_lowpass_2k",
        layout_score=92,
        simulation_verified=True,
    )
    ev = evaluate_candidate(seeded_templates, m.templateId)
    assert ev.duplicate_of == "rc_lowpass"
    codes = _codes(ev.applied_rules)
    assert "RULE_VALUE_ONLY_VARIANT" in codes
    dup_gate = next(g for g in ev.gates if g.code == "GATE_DUPLICATE_TOPOLOGY")
    assert not dup_gate.passed
    # promotion must be blocked
    allowed, blocking = can_promote(ev)
    assert not allowed
    assert any(g.code == "GATE_DUPLICATE_TOPOLOGY" for g in blocking)


def test_value_variant_with_different_topology_is_not_duplicate(seeded_templates: Path) -> None:
    """An RC highpass is not a duplicate of RC lowpass; same topology is
    the key constraint, not same circuit family."""
    ir = _rc_ir(topology="rc_highpass", r1_value="3.18k", c1_value="100n")
    m = create_candidate_from_ir(
        seeded_templates,
        ir,
        template_id="rc_highpass_318k",
        layout_score=92,
        simulation_verified=True,
    )
    ev = evaluate_candidate(seeded_templates, m.templateId)
    assert ev.duplicate_of is None


def test_value_variant_detection_holds_for_different_voltage_divider(seeded_templates: Path) -> None:
    """Same topology but different R values: should be detected as duplicate."""
    ir = _rc_ir(topology="voltage_divider", r1_value="2.2k", c1_value="1k")
    m = create_candidate_from_ir(
        seeded_templates,
        ir,
        template_id="vd_22k",
        layout_score=92,
        simulation_verified=True,
    )
    ev = evaluate_candidate(seeded_templates, m.templateId)
    assert ev.duplicate_of == "voltage_divider"


# ---------------------------------------------------------------------------
# can_promote
# ---------------------------------------------------------------------------


def test_can_promote_true_for_healthy_official(seeded_templates: Path) -> None:
    ev = evaluate_candidate(seeded_templates, "rc_lowpass", status=TemplateStatus.OFFICIAL)
    allowed, blocking = can_promote(ev)
    assert allowed is True
    assert blocking == ()


def test_can_promote_false_for_value_variant(seeded_templates: Path) -> None:
    ir = _rc_ir(r1_value="2k", c1_value="100n")
    m = create_candidate_from_ir(
        seeded_templates,
        ir,
        template_id="rc_lowpass_2k",
        layout_score=92,
        simulation_verified=True,
    )
    ev = evaluate_candidate(seeded_templates, m.templateId)
    allowed, _ = can_promote(ev)
    assert allowed is False


def test_can_promote_false_for_sim_failed(seeded_templates: Path) -> None:
    ir = _rc_ir(r1_value="1.5k", c1_value="100n")
    m = create_candidate_from_ir(
        seeded_templates,
        ir,
        template_id="rc_lowpass_nosim",
        layout_score=92,
        simulation_verified=False,
    )
    ev = evaluate_candidate(seeded_templates, m.templateId)
    allowed, _ = can_promote(ev)
    assert allowed is False


def test_can_promote_false_for_low_layout(seeded_templates: Path) -> None:
    ir = _rc_ir(r1_value="1.6k", c1_value="100n")
    m = create_candidate_from_ir(
        seeded_templates,
        ir,
        template_id="rc_lowpass_low",
        layout_score=50,
        simulation_verified=True,
    )
    ev = evaluate_candidate(seeded_templates, m.templateId)
    allowed, _ = can_promote(ev)
    assert allowed is False


# ---------------------------------------------------------------------------
# promote_candidate
# ---------------------------------------------------------------------------


def test_promote_refuses_value_variant_without_force(seeded_templates: Path) -> None:
    ir = _rc_ir(r1_value="2k", c1_value="100n")
    m = create_candidate_from_ir(
        seeded_templates,
        ir,
        template_id="rc_lowpass_2k",
        layout_score=92,
        simulation_verified=True,
    )
    with pytest.raises(EvaluationError) as exc:
        promote_candidate(seeded_templates, m.templateId)
    assert exc.value.code == ERR_EVAL_PROMOTE_BLOCKED
    # The candidate must still be in candidates/, not official/.
    cand = next(
        t for t in list_templates(seeded_templates, status=TemplateStatus.CANDIDATE)
        if t.templateId == m.templateId
    )
    assert cand.status == TemplateStatus.CANDIDATE


def test_promote_force_records_override_tag(seeded_templates: Path) -> None:
    ir = _rc_ir(r1_value="2k", c1_value="100n")
    m = create_candidate_from_ir(
        seeded_templates,
        ir,
        template_id="rc_lowpass_2k",
        layout_score=92,
        simulation_verified=True,
    )
    new, ev = promote_candidate(seeded_templates, m.templateId, force=True)
    assert new.status == TemplateStatus.OFFICIAL
    assert "force-promoted" in new.tags
    # Re-loading from disk must show the persisted tag.
    reloaded = load_manifest(seeded_templates / "official" / m.templateId / "manifest.json")
    assert "force-promoted" in reloaded.tags
    # And the duplicate gate is still reported in the evaluation so the
    # override is visible in the audit.
    assert ev.duplicate_of == "rc_lowpass"


def test_promote_refuses_sim_failed_without_force(seeded_templates: Path) -> None:
    ir = _rc_ir(r1_value="1.5k", c1_value="100n")
    m = create_candidate_from_ir(
        seeded_templates,
        ir,
        template_id="rc_lowpass_nosim",
        layout_score=92,
        simulation_verified=False,
    )
    with pytest.raises(EvaluationError) as exc:
        promote_candidate(seeded_templates, m.templateId)
    assert exc.value.code == ERR_EVAL_PROMOTE_BLOCKED


def test_promote_refuses_low_layout_without_force(seeded_templates: Path) -> None:
    ir = _rc_ir(r1_value="1.6k", c1_value="100n")
    m = create_candidate_from_ir(
        seeded_templates,
        ir,
        template_id="rc_lowpass_low",
        layout_score=50,
        simulation_verified=True,
    )
    with pytest.raises(EvaluationError) as exc:
        promote_candidate(seeded_templates, m.templateId)
    assert exc.value.code == ERR_EVAL_PROMOTE_BLOCKED


def test_promote_succeeds_for_healthy_candidate() -> None:
    """A candidate that is not a value variant, has high layout score,
    and verified simulation can be promoted without --force.

    All three MVP topologies are seeded by ``seed_default_templates``,
    so any IR with one of those topologies is either a value variant
    or structurally identical to the official. To test the
    "no-conflict promote" path we build a *new* templates root
    without any official templates and put the candidate in there.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "templates"
        root.mkdir()
        ir = _rc_ir(r1_value="3.3k", c1_value="47n")
        m = create_candidate_from_ir(
            root,
            ir,
            template_id="rc_lowpass_33k_47n",
            layout_score=92,
            simulation_verified=True,
        )
        new, _ev = promote_candidate(root, m.templateId)
        assert new.status == TemplateStatus.OFFICIAL
        # Idempotent: promoting an already-official template returns
        # without raising or re-tagging.
        again, _ = promote_candidate(root, m.templateId)
        assert again.status == TemplateStatus.OFFICIAL
        assert "force-promoted" not in again.tags


# ---------------------------------------------------------------------------
# audit_promotability + evaluate_all
# ---------------------------------------------------------------------------


def test_evaluate_all_iterates_candidates_in_id_order(seeded_templates: Path) -> None:
    for r1, c1 in [("1.1k", "47n"), ("2.2k", "47n"), ("3.3k", "100n")]:
        create_candidate_from_ir(
            seeded_templates,
            _rc_ir(r1_value=r1, c1_value=c1),
            template_id=f"rc_lowpass_{r1}_{c1}".replace(".", "_"),
            layout_score=92,
            simulation_verified=True,
        )
    results = evaluate_all(seeded_templates, status=TemplateStatus.CANDIDATE)
    ids = [r.template_id for r in results]
    assert ids == sorted(ids)
    assert len(results) >= 3


def test_audit_promotability_counts_decisions(seeded_templates: Path) -> None:
    # healthy candidate (promotable) — uses a new topology name so it
    # does not collide with the seeded ones.
    create_candidate_from_ir(
        seeded_templates,
        _rc_ir(r1_value="4.7k", c1_value="100n"),
        template_id="rc_lowpass_47k",
        layout_score=92,
        simulation_verified=True,
    )
    # candidate that fails sim
    create_candidate_from_ir(
        seeded_templates,
        _rc_ir(r1_value="1.1k", c1_value="100n"),
        template_id="rc_lowpass_11k",
        layout_score=92,
        simulation_verified=False,
    )
    # candidate with low layout
    create_candidate_from_ir(
        seeded_templates,
        _rc_ir(r1_value="1.3k", c1_value="100n"),
        template_id="rc_lowpass_13k",
        layout_score=60,
        simulation_verified=True,
    )
    # value variant
    create_candidate_from_ir(
        seeded_templates,
        _rc_ir(r1_value="2k", c1_value="100n"),
        template_id="rc_lowpass_2k",
        layout_score=92,
        simulation_verified=True,
    )
    rep = audit_promotability(seeded_templates, status=TemplateStatus.CANDIDATE)
    assert isinstance(rep, PromotabilityReport)
    # Sum of counts == number of evaluated templates.
    assert sum(rep.counts.values()) == len(rep.evaluated)
    # At least one candidate has a failing gate.
    assert rep.blocking
    # The "official" bucket is 0 because none of our value-variants
    # or low-layout candidates earn it.
    assert rep.counts[PromotionDecision.OFFICIAL.value] == 0


# ---------------------------------------------------------------------------
# EvaluationResult shape
# ---------------------------------------------------------------------------


def test_evaluation_result_to_dict_shape(seeded_templates: Path) -> None:
    ev = evaluate_candidate(seeded_templates, "rc_lowpass", status=TemplateStatus.OFFICIAL)
    d = ev.to_dict()
    assert d["schemaVersion"] == EVALUATION_SCHEMA_VERSION
    assert d["templateId"] == "rc_lowpass"
    assert d["status"] == "official"
    assert d["decision"] in {d.value for d in PromotionDecision}
    assert isinstance(d["appliedRules"], list)
    assert isinstance(d["gates"], list)
    assert d["manifest"]["templateId"] == "rc_lowpass"


def test_evaluation_result_failed_gates_helper(seeded_templates: Path) -> None:
    """``failed_gates`` is the tuple of :class:`GateCheck` that did not
    pass, and ``blocking_reasons`` returns just the codes for compact
    use in CLI text output.

    Uses a topology that is *not* covered by an existing official so
    the duplicate gate does not fire (otherwise the failed-gates
    tuple contains both ``GATE_DUPLICATE_TOPOLOGY`` and the layout
    gate we are testing). We build a *new* candidate from scratch
    that has no official counterpart.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "templates"
        root.mkdir()
        # No official templates at all in this fresh dir, so the
        # duplicate gate cannot fire.
        data = _rc_ir(r1_value="1k", c1_value="100n")
        create_candidate_from_ir(
            root,
            data,
            template_id="rc_lowpass_test",
            layout_score=50,  # below 70
            simulation_verified=True,
        )
        ev = evaluate_candidate(root, "rc_lowpass_test")
        assert any(g.code == "GATE_LAYOUT_TOO_LOW" for g in ev.failed_gates)
        assert ev.blocking_reasons == ("GATE_LAYOUT_TOO_LOW",)


def test_evaluation_result_decision_thresholds(seeded_templates: Path) -> None:
    """A manually-constructed manifest with no real IR drives each threshold.

    * A *high-quality* manifest on a fresh templates root lands in
      the OFFICIAL bucket (no other official exists to conflict).
    * A *value variant* of an existing official lands in the PROJECT
      bucket: it gets the -3 penalty plus the failing duplicate
      gate, dragging the score below the CANDIDATE threshold.
    """
    import tempfile

    # ---- high quality -> OFFICIAL ------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "templates"
        root.mkdir()
        manifest = TemplateManifest(
            templateId="t_high",
            schemaVersion=TEMPLATES_SCHEMA_VERSION,
            name="t_high",
            topology="voltage_divider",
            status=TemplateStatus.CANDIDATE,
            description="save: high-quality reusable voltage divider",
            parameters={
                "R1": TemplateParameter(
                    description="series resistor", default="1k", editable=True
                )
            },
            layoutScore=92,
            simulationVerified=True,
        )
        from ltagent.templates import _manifest_path, dump_manifest

        (root / "candidates" / "t_high").mkdir(parents=True)
        (root / "candidates" / "t_high" / "template.ir.json").write_text(
            json.dumps(
                _rc_ir(topology="voltage_divider", r1_value="1k", c1_value="1k")
            ),
            encoding="utf-8",
        )
        dump_manifest(manifest, _manifest_path(root, TemplateStatus.CANDIDATE, "t_high"))
        ev = evaluate_candidate(root, "t_high")
        assert ev.decision == PromotionDecision.OFFICIAL

    # ---- value variant -> PROJECT ------------------------------------
    # The seeded `voltage_divider` is official; create a value variant
    # of it on the same root. Score = +2 (topology) -3 (value only)
    # -1 (incomplete) = -2, below the CANDIDATE threshold of 3.
    ir = _rc_ir(topology="voltage_divider", r1_value="9k", c1_value="1k")
    create_candidate_from_ir(
        seeded_templates,
        ir,
        template_id="vd_variant_for_project_bucket",
        layout_score=None,  # no layout score recorded
        simulation_verified=False,  # not verified
    )
    ev2 = evaluate_candidate(seeded_templates, "vd_variant_for_project_bucket")
    assert ev2.decision == PromotionDecision.PROJECT
    assert ev2.duplicate_of == "voltage_divider"


# ---------------------------------------------------------------------------
# Defensive: bad input is rejected with stable codes
# ---------------------------------------------------------------------------


def test_template_id_pattern_still_enforced(seeded_templates: Path) -> None:
    # Sanity: the id pattern the evaluator relies on matches the
    # templates module's pattern. If a future refactor changes one
    # but not the other, this test will fail loudly.
    assert TEMPLATE_ID_PATTERN.pattern == r"^[a-z][a-z0-9_]{0,63}$"
