"""Unit tests for ``ltagent.templates`` (Phase 6).

The tests cover the acceptance criteria from the project plan:

* Manifest read/write round-trip
* ``list`` / ``show`` / ``match`` / ``audit``
* Same topology with different values is recognised as a value variant
  and does **not** produce a duplicate official template
* Use-count increments are observable and idempotent at the data level
* Path traversal and malformed manifests are rejected with stable
  error codes

All tests are offline. No LTspice / Wine involvement.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from ltagent.ir import load_ir
from ltagent.templates import (
    ERR_TEMPLATE_DUPLICATE,
    ERR_TEMPLATE_INVALID,
    ERR_TEMPLATE_NOT_FOUND,
    ERR_TEMPLATE_PATH_TRAVERSAL,
    TEMPLATES_SCHEMA_VERSION,
    AuditReport,
    MatchResult,
    TemplateError,
    TemplateManifest,
    TemplateParameter,
    TemplateStatus,
    audit_templates,
    create_candidate_from_ir,
    dump_manifest,
    find_by_topology,
    increment_use_count,
    list_templates,
    load_manifest,
    match_template,
    move_template,
    seed_default_templates,
    show_template,
    write_index,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
RC_LOWPASS_EXAMPLE = REPO_ROOT / "examples" / "rc_lowpass.ir.json"

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


# ---------------------------------------------------------------------------
# Manifest IO
# ---------------------------------------------------------------------------


def test_manifest_round_trip(tmp_path: Path) -> None:
    manifest = TemplateManifest(
        templateId="rc_lowpass",
        schemaVersion=TEMPLATES_SCHEMA_VERSION,
        name="RC Low-Pass Filter",
        topology="rc_lowpass",
        status=TemplateStatus.OFFICIAL,
        tags=("filter", "rc"),
        description="first-order RC",
        files={"ir": "template.ir.json"},
        parameters={
            "R1": TemplateParameter(description="series", default="1k"),
            "C1": TemplateParameter(description="shunt", default="100n"),
        },
        formula={"cutoff": "1/(2*pi*R*C)"},
        layoutScore=88,
        simulationVerified=True,
        useCount=0,
        createdAt="2026-06-17",
        updatedAt="2026-06-17",
    )
    path = tmp_path / "manifest.json"
    dump_manifest(manifest, path)
    assert path.is_file()
    raw = json.loads(path.read_text(encoding="utf-8"))
    # JSON Schema-style "extra=forbid" behaviour is not present; we just
    # check the round-trip preserves the data.
    assert raw["templateId"] == "rc_lowpass"
    assert raw["status"] == "official"
    assert raw["parameters"]["R1"]["default"] == "1k"

    loaded = load_manifest(path)
    assert loaded == manifest
    assert loaded.parameters["R1"].default == "1k"


def test_load_manifest_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(TemplateError) as exc:
        load_manifest(tmp_path / "nope.json")
    assert exc.value.code == ERR_TEMPLATE_NOT_FOUND


def test_load_manifest_invalid_json_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("not json", encoding="utf-8")
    with pytest.raises(TemplateError) as exc:
        load_manifest(p)
    assert exc.value.code == ERR_TEMPLATE_INVALID


def test_load_manifest_wrong_shape_raises(tmp_path: Path) -> None:
    p = tmp_path / "list.json"
    p.write_text("[]", encoding="utf-8")
    with pytest.raises(TemplateError) as exc:
        load_manifest(p)
    assert exc.value.code == ERR_TEMPLATE_INVALID


def test_template_id_pattern_rejects_path_traversal() -> None:
    with pytest.raises(TemplateError) as exc:
        show_template("/tmp", "../../etc/passwd")
    # Either ID_INVALID or NOT_FOUND is acceptable; the path-traversal
    # style ID should fail validation first.
    assert exc.value.code in ("TEMPLATE_ID_INVALID", ERR_TEMPLATE_NOT_FOUND)


def test_dump_manifest_writes_atomically(tmp_path: Path) -> None:
    manifest = TemplateManifest(
        templateId="x",
        schemaVersion=TEMPLATES_SCHEMA_VERSION,
        name="X",
        topology="voltage_divider",
        status=TemplateStatus.CANDIDATE,
    )
    p = tmp_path / "manifest.json"
    dump_manifest(manifest, p)
    assert p.is_file()
    # No leftover .tmp files.
    leftovers = list(tmp_path.glob(".manifest_*.tmp"))
    assert not leftovers


# ---------------------------------------------------------------------------
# Status enum
# ---------------------------------------------------------------------------


def test_status_from_str_accepts_aliases() -> None:
    assert TemplateStatus.from_str("official") == TemplateStatus.OFFICIAL
    assert TemplateStatus.from_str("candidate") == TemplateStatus.CANDIDATE
    assert TemplateStatus.from_str("candidates") == TemplateStatus.CANDIDATE
    assert TemplateStatus.from_str("rejected") == TemplateStatus.REJECTED


def test_status_from_str_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        TemplateStatus.from_str("nope")


# ---------------------------------------------------------------------------
# Default seed
# ---------------------------------------------------------------------------


def test_seed_creates_ten_official_templates(seeded_templates: Path) -> None:
    items = list_templates(seeded_templates)
    assert len(items) == 10
    ids = {m.templateId for m in items}
    # Phase 0/6/8 trio.
    assert {"voltage_divider", "rc_lowpass", "rc_highpass"} <= ids
    # Phase 11 analog set.
    assert {
        "inverting_opamp",
        "noninv_opamp",
        "comparator",
        "diode_clipper",
        "halfwave_rectifier",
        "bridge_rectifier",
        "transistor_switch",
    } <= ids
    assert all(m.status == TemplateStatus.OFFICIAL for m in items)


def test_seed_writes_ir_files(seeded_templates: Path) -> None:
    items = list_templates(seeded_templates)
    for m in items:
        ir_path = seeded_templates / m.status.value / m.templateId / "template.ir.json"
        assert ir_path.is_file()
        # The IR must be valid.
        ir = load_ir(ir_path)
        assert ir.topology == m.topology


def test_seed_writes_index(seeded_templates: Path) -> None:
    assert (seeded_templates / "index.json").is_file()
    index = json.loads((seeded_templates / "index.json").read_text(encoding="utf-8"))
    assert "official" in index["byStatus"]
    assert set(index["byStatus"]["official"]) == {
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


def test_seed_is_idempotent(seeded_templates: Path) -> None:
    written = seed_default_templates(seeded_templates)
    assert written == []
    items = list_templates(seeded_templates)
    assert len(items) == 10


def test_ensure_seeds_empty_workspace(templates_root: Path) -> None:
    """A fresh empty templates dir is populated on first call."""
    from ltagent.templates import (
        OFFICIAL_TEMPLATE_COUNT,
        ensure_default_templates,
    )

    assert not any(templates_root.iterdir())
    written = ensure_default_templates(templates_root)
    assert len(written) == OFFICIAL_TEMPLATE_COUNT == 10
    manifests = list_templates(templates_root, status=TemplateStatus.OFFICIAL)
    assert len(manifests) == 10


def test_ensure_completes_partial_library(templates_root: Path) -> None:
    """A library that already has some seeds gets only the missing ones.

    Simulates an upgrade from Phase 6 (3 MVP templates) to Phase 11
    (10 templates): ``ensure_default_templates`` adds the seven new
    entries without overwriting the existing three.
    """
    from ltagent.templates import (
        TemplateStatus,
        _default_seeds,
        dump_manifest,
        ensure_default_templates,
    )

    # Seed only the 3 MVP templates (a pre-Phase-11 library).
    for seed in _default_seeds()[:3]:
        d = templates_root / seed.status.value / seed.templateId
        d.mkdir(parents=True, exist_ok=True)
        (d / "template.ir.json").write_text(
            json.dumps({"schemaVersion": "0.1", "name": seed.templateId, "topology": seed.topology}),
            encoding="utf-8",
        )
        dump_manifest(seed, d / "manifest.json")

    officials_before = list_templates(templates_root, status=TemplateStatus.OFFICIAL)
    assert len(officials_before) == 3

    # Run ensure again; the 7 new analog templates should appear.
    written = ensure_default_templates(templates_root)
    assert len(written) == 7
    officials_after = list_templates(templates_root, status=TemplateStatus.OFFICIAL)
    assert len(officials_after) == 10
    # And a third call is a clean no-op.
    assert ensure_default_templates(templates_root) == []


def test_ensure_does_not_overwrite_existing(templates_root: Path) -> None:
    """A user-modified manifest is never clobbered by the auto-seed.

    The auto-seed is intended to be additive: it only writes manifests
    that are missing. A user who has already edited a manifest in
    place keeps their changes.
    """
    from dataclasses import replace

    from ltagent.templates import dump_manifest, ensure_default_templates, load_manifest

    seed_default_templates(templates_root)
    mp = templates_root / "official" / "rc_lowpass" / "manifest.json"
    original = load_manifest(mp)
    edited = replace(original, description="USER EDITED")
    dump_manifest(edited, mp)

    written = ensure_default_templates(templates_root)
    assert written == []  # no missing seeds
    after = load_manifest(mp)
    assert after.description == "USER EDITED"


# ---------------------------------------------------------------------------
# list / show / find_by_topology
# ---------------------------------------------------------------------------


def test_list_filters_by_status(seeded_templates: Path) -> None:
    officials = list_templates(seeded_templates, status=TemplateStatus.OFFICIAL)
    assert {m.templateId for m in officials} == {
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
    candidates = list_templates(seeded_templates, status=TemplateStatus.CANDIDATE)
    assert candidates == []


def test_list_accepts_string_status(seeded_templates: Path) -> None:
    items = list_templates(seeded_templates, status="official")
    assert len(items) == 10


def test_list_creates_missing_dirs(tmp_path: Path) -> None:
    root = tmp_path / "fresh"
    # _ensure_root creates the root + status sub-dirs.
    items = list_templates(root)
    assert items == []
    assert (root / "official").is_dir()
    assert (root / "candidates").is_dir()
    assert (root / "rejected").is_dir()


def test_show_template_returns_full_manifest(seeded_templates: Path) -> None:
    m = show_template(seeded_templates, "rc_lowpass")
    assert m.templateId == "rc_lowpass"
    assert m.topology == "rc_lowpass"
    assert m.status == TemplateStatus.OFFICIAL
    assert "cutoffFrequency" in m.formula


def test_show_template_missing_raises(seeded_templates: Path) -> None:
    with pytest.raises(TemplateError) as exc:
        show_template(seeded_templates, "does_not_exist")
    assert exc.value.code == ERR_TEMPLATE_NOT_FOUND


def test_find_by_topology_returns_match(seeded_templates: Path) -> None:
    m = find_by_topology(seeded_templates, "rc_lowpass")
    assert m is not None
    assert m.templateId == "rc_lowpass"


def test_find_by_topology_returns_none_when_absent(seeded_templates: Path) -> None:
    assert find_by_topology(seeded_templates, "rc_bandpass") is None


# ---------------------------------------------------------------------------
# match: the core acceptance criterion
# ---------------------------------------------------------------------------


def test_match_returns_official_for_same_topology(seeded_templates: Path) -> None:
    ir = load_ir(RC_LOWPASS_EXAMPLE)
    result = match_template(seeded_templates, ir, bump=False)
    assert isinstance(result, MatchResult)
    assert result.matched is True
    assert result.template is not None
    assert result.template.templateId == "rc_lowpass"
    assert result.isValueVariant is False  # example IR matches the seed exactly


def test_match_detects_value_variant_for_same_topology(seeded_templates: Path) -> None:
    """Different values, same topology => value variant, no duplicate."""
    data = {
        "schemaVersion": "0.1",
        "name": "rc_lowpass_500hz",
        "topology": "rc_lowpass",
        "nodes": ["in", "out", "0"],
        "components": [
            {
                "id": "Vin",
                "kind": "voltage_source",
                "spicePrefix": "V",
                "nodes": ["in", "0"],
                "value": "SINE(0 1 500)",
            },
            {
                "id": "R1",
                "kind": "resistor",
                "spicePrefix": "R",
                "nodes": ["in", "out"],
                "value": "3.18k",
            },
            {
                "id": "C1",
                "kind": "capacitor",
                "spicePrefix": "C",
                "nodes": ["out", "0"],
                "value": "100n",
            },
        ],
        "analysis": [{"kind": "tran", "stopTime": "10m"}],
        "measurements": [],
    }
    result = match_template(seeded_templates, data, bump=False)
    assert result.matched is True
    assert result.template is not None
    assert result.template.templateId == "rc_lowpass"
    assert result.isValueVariant is True
    assert result.useCount == 0  # bump=False


def test_match_no_template_for_unknown_topology(seeded_templates: Path) -> None:
    data = {
        "schemaVersion": "0.1",
        "name": "rc_bandpass_demo",
        "topology": "rc_bandpass",  # not in seed
        "nodes": ["in", "mid", "out", "0"],
        "components": [
            {
                "id": "R1",
                "kind": "resistor",
                "spicePrefix": "R",
                "nodes": ["in", "mid"],
                "value": "1k",
            },
            {
                "id": "C1",
                "kind": "capacitor",
                "spicePrefix": "C",
                "nodes": ["mid", "out"],
                "value": "100n",
            },
            {
                "id": "R2",
                "kind": "resistor",
                "spicePrefix": "R",
                "nodes": ["mid", "0"],
                "value": "1k",
            },
        ],
        "analysis": [{"kind": "op"}],
    }
    result = match_template(seeded_templates, data, bump=False)
    assert result.matched is False
    assert result.template is None
    assert "rc_bandpass" in result.reason


def test_match_bumps_use_count(seeded_templates: Path) -> None:
    ir = load_ir(RC_LOWPASS_EXAMPLE)
    r1 = match_template(seeded_templates, ir, bump=True)
    assert r1.useCount == 1
    assert r1.useCountBumped is True
    r2 = match_template(seeded_templates, ir, bump=True)
    assert r2.useCount == 2
    assert r2.useCountBumped is True


def test_match_no_bump_keeps_count(seeded_templates: Path) -> None:
    ir = load_ir(RC_LOWPASS_EXAMPLE)
    r1 = match_template(seeded_templates, ir, bump=False)
    assert r1.useCount == 0
    assert r1.useCountBumped is False
    r2 = match_template(seeded_templates, ir, bump=True)
    assert r2.useCount == 1


def test_match_dict_input_works(seeded_templates: Path) -> None:
    data = {
        "schemaVersion": "0.1",
        "name": "vd",
        "topology": "voltage_divider",
        "nodes": ["in", "out", "0"],
        "components": [
            {
                "id": "Vin",
                "kind": "voltage_source",
                "spicePrefix": "V",
                "nodes": ["in", "0"],
                "value": "DC 12",
            },
            {
                "id": "R1",
                "kind": "resistor",
                "spicePrefix": "R",
                "nodes": ["in", "out"],
                "value": "1.4k",
            },
            {
                "id": "R2",
                "kind": "resistor",
                "spicePrefix": "R",
                "nodes": ["out", "0"],
                "value": "1k",
            },
        ],
        "analysis": [{"kind": "op"}],
    }
    r = match_template(seeded_templates, data, bump=False)
    assert r.matched is True
    assert r.template is not None
    assert r.template.templateId == "voltage_divider"


def test_match_invalid_ir_input_raises(seeded_templates: Path) -> None:
    with pytest.raises(TemplateError) as exc:
        match_template(seeded_templates, {"topology": 123}, bump=False)  # type: ignore[arg-type]
    assert exc.value.code == ERR_TEMPLATE_INVALID


# ---------------------------------------------------------------------------
# use count
# ---------------------------------------------------------------------------


def test_increment_use_count_returns_new_value(seeded_templates: Path) -> None:
    n = increment_use_count(seeded_templates, "rc_lowpass")
    assert n == 1
    n = increment_use_count(seeded_templates, "rc_lowpass")
    assert n == 2


def test_increment_use_count_persists(seeded_templates: Path) -> None:
    increment_use_count(seeded_templates, "rc_lowpass")
    increment_use_count(seeded_templates, "rc_lowpass")
    reloaded = show_template(seeded_templates, "rc_lowpass")
    assert reloaded.useCount == 2


def test_increment_use_count_missing_raises(seeded_templates: Path) -> None:
    with pytest.raises(TemplateError) as exc:
        increment_use_count(seeded_templates, "does_not_exist")
    assert exc.value.code == ERR_TEMPLATE_NOT_FOUND


# ---------------------------------------------------------------------------
# Candidate creation
# ---------------------------------------------------------------------------


def test_create_candidate_from_ir(seeded_templates: Path) -> None:
    data = {
        "schemaVersion": "0.1",
        "name": "my_inverting_amp",
        "topology": "voltage_divider",
        "nodes": ["in", "out", "0"],
        "components": [
            {
                "id": "Vin",
                "kind": "voltage_source",
                "spicePrefix": "V",
                "nodes": ["in", "0"],
                "value": "DC 5",
            },
            {
                "id": "R1",
                "kind": "resistor",
                "spicePrefix": "R",
                "nodes": ["in", "out"],
                "value": "10k",
            },
            {
                "id": "R2",
                "kind": "resistor",
                "spicePrefix": "R",
                "nodes": ["out", "0"],
                "value": "10k",
            },
        ],
        "analysis": [{"kind": "op"}],
    }
    m = create_candidate_from_ir(
        seeded_templates,
        data,
        layout_score=80,
        simulation_verified=True,
        description="vdiv demo",
        tags=("demo",),
    )
    assert m.status == TemplateStatus.CANDIDATE
    assert m.templateId == "my_inverting_amp"  # derived from ir.name
    assert m.layoutScore == 80
    assert m.simulationVerified is True
    # The directory and IR file should exist.
    cand_dir = seeded_templates / "candidates" / "my_inverting_amp"
    assert cand_dir.is_dir()
    assert (cand_dir / "manifest.json").is_file()
    assert (cand_dir / "template.ir.json").is_file()


def test_create_candidate_duplicate_rejected(seeded_templates: Path) -> None:
    """The duplicate-check fires on a same-status collision too."""
    data = {
        "schemaVersion": "0.1",
        "name": "vd_clone",  # safe, never seeded
        "topology": "voltage_divider",
        "nodes": ["in", "out", "0"],
        "components": [
            {
                "id": "Vin",
                "kind": "voltage_source",
                "spicePrefix": "V",
                "nodes": ["in", "0"],
                "value": "DC 12",
            },
            {
                "id": "R1",
                "kind": "resistor",
                "spicePrefix": "R",
                "nodes": ["in", "out"],
                "value": "1k",
            },
            {
                "id": "R2",
                "kind": "resistor",
                "spicePrefix": "R",
                "nodes": ["out", "0"],
                "value": "1k",
            },
        ],
        "analysis": [{"kind": "op"}],
    }
    create_candidate_from_ir(seeded_templates, data)
    with pytest.raises(TemplateError) as exc:
        create_candidate_from_ir(seeded_templates, data)
    assert exc.value.code == ERR_TEMPLATE_DUPLICATE


def test_create_candidate_invalid_ir_rejected(seeded_templates: Path) -> None:
    with pytest.raises(TemplateError) as exc:
        create_candidate_from_ir(seeded_templates, {"bogus": True})  # type: ignore[arg-type]
    assert exc.value.code == ERR_TEMPLATE_INVALID


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


def test_move_candidate_to_official(seeded_templates: Path) -> None:
    data = {
        "schemaVersion": "0.1",
        "name": "promote_me",
        "topology": "voltage_divider",
        "nodes": ["in", "out", "0"],
        "components": [
            {
                "id": "Vin",
                "kind": "voltage_source",
                "spicePrefix": "V",
                "nodes": ["in", "0"],
                "value": "DC 12",
            },
            {
                "id": "R1",
                "kind": "resistor",
                "spicePrefix": "R",
                "nodes": ["in", "out"],
                "value": "1k",
            },
            {
                "id": "R2",
                "kind": "resistor",
                "spicePrefix": "R",
                "nodes": ["out", "0"],
                "value": "1k",
            },
        ],
        "analysis": [{"kind": "op"}],
    }
    create_candidate_from_ir(seeded_templates, data)
    moved = move_template(
        seeded_templates, "promote_me", to_status=TemplateStatus.OFFICIAL
    )
    assert moved.status == TemplateStatus.OFFICIAL
    assert not (seeded_templates / "candidates" / "promote_me").exists()
    assert (seeded_templates / "official" / "promote_me").is_dir()
    # And it's now findable via the official list.
    items = list_templates(seeded_templates, status=TemplateStatus.OFFICIAL)
    assert any(m.templateId == "promote_me" for m in items)


def test_move_official_to_rejected(seeded_templates: Path) -> None:
    moved = move_template(
        seeded_templates, "rc_lowpass", to_status=TemplateStatus.REJECTED
    )
    assert moved.status == TemplateStatus.REJECTED
    assert not (seeded_templates / "official" / "rc_lowpass").exists()
    assert (seeded_templates / "rejected" / "rc_lowpass").is_dir()


def test_move_missing_raises(seeded_templates: Path) -> None:
    with pytest.raises(TemplateError) as exc:
        move_template(seeded_templates, "no_such_template", to_status=TemplateStatus.OFFICIAL)
    assert exc.value.code == ERR_TEMPLATE_NOT_FOUND


def test_create_candidate_rejects_existing_official_id(seeded_templates: Path) -> None:
    """The same id in any status directory must block candidate creation."""
    data = {
        "schemaVersion": "0.1",
        "name": "rc_lowpass",  # already an official id
        "topology": "rc_lowpass",
        "nodes": ["in", "out", "0"],
        "components": [
            {
                "id": "Vin",
                "kind": "voltage_source",
                "spicePrefix": "V",
                "nodes": ["in", "0"],
                "value": "SINE(0 1 1k)",
            },
            {
                "id": "R1",
                "kind": "resistor",
                "spicePrefix": "R",
                "nodes": ["in", "out"],
                "value": "1.59k",
            },
            {
                "id": "C1",
                "kind": "capacitor",
                "spicePrefix": "C",
                "nodes": ["out", "0"],
                "value": "100n",
            },
        ],
        "analysis": [{"kind": "tran", "stopTime": "5m"}],
    }
    with pytest.raises(TemplateError) as exc:
        create_candidate_from_ir(seeded_templates, data)
    assert exc.value.code == ERR_TEMPLATE_DUPLICATE


def test_move_destination_collision_rejected(seeded_templates: Path) -> None:
    """move_template refuses to clobber an existing template at the destination."""
    data = {
        "schemaVersion": "0.1",
        "name": "rc_lowpass_clone",  # a fresh id
        "topology": "rc_lowpass",
        "nodes": ["in", "out", "0"],
        "components": [
            {
                "id": "Vin",
                "kind": "voltage_source",
                "spicePrefix": "V",
                "nodes": ["in", "0"],
                "value": "SINE(0 1 1k)",
            },
            {
                "id": "R1",
                "kind": "resistor",
                "spicePrefix": "R",
                "nodes": ["in", "out"],
                "value": "1.59k",
            },
            {
                "id": "C1",
                "kind": "capacitor",
                "spicePrefix": "C",
                "nodes": ["out", "0"],
                "value": "100n",
            },
        ],
        "analysis": [{"kind": "tran", "stopTime": "5m"}],
    }
    create_candidate_from_ir(seeded_templates, data)
    # Manually re-create an `rc_lowpass_clone` entry in the official dir
    # so the move would clobber an existing destination.
    official_clone = seeded_templates / "official" / "rc_lowpass_clone"
    official_clone.mkdir()
    dump_manifest(
        TemplateManifest(
            templateId="rc_lowpass_clone",
            schemaVersion=TEMPLATES_SCHEMA_VERSION,
            name="Already there",
            topology="rc_lowpass",
            status=TemplateStatus.OFFICIAL,
        ),
        official_clone / "manifest.json",
    )
    with pytest.raises(TemplateError) as exc:
        move_template(
            seeded_templates,
            "rc_lowpass_clone",
            to_status=TemplateStatus.OFFICIAL,
        )
    assert exc.value.code == ERR_TEMPLATE_DUPLICATE


def test_move_same_status_is_noop(seeded_templates: Path) -> None:
    m = move_template(seeded_templates, "rc_lowpass", to_status=TemplateStatus.OFFICIAL)
    assert m.status == TemplateStatus.OFFICIAL
    assert (seeded_templates / "official" / "rc_lowpass").is_dir()


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def test_audit_reports_clean_state(seeded_templates: Path) -> None:
    report = audit_templates(seeded_templates)
    assert isinstance(report, AuditReport)
    d = report.to_dict()
    assert d["counts"]["official"] == 10
    assert d["counts"]["candidates"] == 0
    assert d["counts"]["rejected"] == 0
    assert d["duplicates"] == []
    assert d["warnings"] == []
    assert d["indexed"] is True


def test_audit_detects_duplicate_topology(seeded_templates: Path) -> None:
    data = {
        "schemaVersion": "0.1",
        "name": "rc_lowpass_alt",
        "topology": "rc_lowpass",  # same topology as the official template
        "nodes": ["in", "out", "0"],
        "components": [
            {
                "id": "Vin",
                "kind": "voltage_source",
                "spicePrefix": "V",
                "nodes": ["in", "0"],
                "value": "SINE(0 1 1k)",
            },
            {
                "id": "R1",
                "kind": "resistor",
                "spicePrefix": "R",
                "nodes": ["in", "out"],
                "value": "1.59k",
            },
            {
                "id": "C1",
                "kind": "capacitor",
                "spicePrefix": "C",
                "nodes": ["out", "0"],
                "value": "100n",
            },
        ],
        "analysis": [{"kind": "tran", "stopTime": "5m"}],
    }
    create_candidate_from_ir(seeded_templates, data)
    report = audit_templates(seeded_templates)
    d = report.to_dict()
    # Cross-status duplicates are listed in topology counts but the
    # per-status duplicate detection should NOT flag the two templates
    # because they live in different status directories.
    assert d["duplicates"] == []
    assert d["topologies"]["rc_lowpass"] == 2


def test_audit_detects_within_status_duplicate(seeded_templates: Path) -> None:
    """Two official templates sharing a topology is the bad case."""
    data = {
        "schemaVersion": "0.1",
        "name": "rc_lowpass_dup",
        "topology": "rc_lowpass",
        "nodes": ["in", "out", "0"],
        "components": [
            {
                "id": "Vin",
                "kind": "voltage_source",
                "spicePrefix": "V",
                "nodes": ["in", "0"],
                "value": "SINE(0 1 1k)",
            },
            {
                "id": "R1",
                "kind": "resistor",
                "spicePrefix": "R",
                "nodes": ["in", "out"],
                "value": "1.59k",
            },
            {
                "id": "C1",
                "kind": "capacitor",
                "spicePrefix": "C",
                "nodes": ["out", "0"],
                "value": "100n",
            },
        ],
        "analysis": [{"kind": "tran", "stopTime": "5m"}],
    }
    create_candidate_from_ir(seeded_templates, data)
    move_template(seeded_templates, "rc_lowpass_dup", to_status=TemplateStatus.OFFICIAL)
    report = audit_templates(seeded_templates)
    d = report.to_dict()
    assert any(
        dup["topology"] == "rc_lowpass" for dup in d["duplicates"]
    )


def test_audit_surfaces_broken_manifest(seeded_templates: Path) -> None:
    bad = seeded_templates / "official" / "broken"
    bad.mkdir()
    (bad / "manifest.json").write_text("{not json", encoding="utf-8")
    report = audit_templates(seeded_templates)
    codes = {w["code"] for w in report.warnings}
    assert ERR_TEMPLATE_INVALID in codes


def test_audit_surfaces_missing_ir(seeded_templates: Path) -> None:
    d = seeded_templates / "official" / "no_ir"
    d.mkdir()
    manifest = TemplateManifest(
        templateId="no_ir",
        schemaVersion=TEMPLATES_SCHEMA_VERSION,
        name="No IR",
        topology="voltage_divider",
        status=TemplateStatus.OFFICIAL,
    )
    dump_manifest(manifest, d / "manifest.json")
    report = audit_templates(seeded_templates)
    codes = {w["code"] for w in report.warnings}
    assert "TEMPLATE_NO_IR" in codes


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


def test_write_index_round_trip(seeded_templates: Path) -> None:
    write_index(seeded_templates)
    index = json.loads((seeded_templates / "index.json").read_text(encoding="utf-8"))
    assert "official" in index["byStatus"]
    assert set(index["byStatus"]["official"]) == {
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


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def test_template_id_with_slash_rejected(seeded_templates: Path) -> None:
    with pytest.raises(TemplateError) as exc:
        show_template(seeded_templates, "../escape")
    assert exc.value.code in (
        "TEMPLATE_ID_INVALID",
        ERR_TEMPLATE_NOT_FOUND,
        ERR_TEMPLATE_PATH_TRAVERSAL,
    )


def test_match_rejects_unsupported_topology_with_clear_error(
    seeded_templates: Path,
) -> None:
    # We do not raise an error here: a missing topology is just "no
    # match". But seeding-time and create-candidate paths do enforce
    # the IR contract via CircuitIR.
    bad = {
        "schemaVersion": "0.1",
        "name": "bad",
        "topology": "no_such_topology",
        "nodes": ["in", "0"],
        "components": [
            {
                "id": "R1",
                "kind": "resistor",
                "spicePrefix": "R",
                "nodes": ["in", "0"],
                "value": "1k",
            }
        ],
        "analysis": [{"kind": "op"}],
    }
    result = match_template(seeded_templates, bad, bump=False)
    assert result.matched is False


# ---------------------------------------------------------------------------
# Bridge: create_candidate_from_project (Phase 7 <-> Phase 9)
# ---------------------------------------------------------------------------


def _write_project(
    project_dir: Path,
    *,
    ir: dict,
    run_success: bool = True,
    layout_score: int | None = 92,
) -> None:
    """Materialise a minimal project directory for the bridge tests.

    The layout is exactly what ``ltagent create`` writes:

    * ``circuit.ir.json``
    * ``result.json`` with ``run.success`` and ``layoutScore``
    """
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "circuit.ir.json").write_text(
        json.dumps(ir, indent=2) + "\n", encoding="utf-8"
    )
    result = {
        "schemaVersion": "0.1",
        "projectId": project_dir.name,
        "success": run_success,
        "run": {
            "attempted": True,
            "success": run_success,
            "timeoutSeconds": 30,
            "durationMs": 100,
            "exitCode": 0 if run_success else 1,
        },
        "measurements": {},
        "assertions": [],
        "layout": {"score": layout_score, "warnings": []} if layout_score is not None else {"warnings": []},
        "warnings": [],
        "errors": [],
    }
    if layout_score is not None:
        result["layoutScore"] = layout_score
    (project_dir / "result.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )


def test_create_candidate_from_project_succeeds(tmp_path: Path) -> None:
    """A successful project with a recorded layout score becomes a candidate."""
    from ltagent.templates import create_candidate_from_project

    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    project_dir = tmp_path / "projects" / "p1"
    ir = {
        "schemaVersion": "0.1",
        "name": "my_vdiv",
        "topology": "voltage_divider",
        "description": "Reusable voltage divider",
        "nodes": ["in", "out", "0"],
        "components": [
            {"id": "Vin", "kind": "voltage_source", "spicePrefix": "V",
             "nodes": ["in", "0"], "value": "DC 12", "role": "input_source"},
            {"id": "R1", "kind": "resistor", "spicePrefix": "R",
             "nodes": ["in", "out"], "value": "1k", "role": "series_resistor"},
            {"id": "R2", "kind": "resistor", "spicePrefix": "R",
             "nodes": ["out", "0"], "value": "1k", "role": "shunt_resistor"},
        ],
        "analysis": [{"kind": "op"}],
    }
    _write_project(project_dir, ir=ir, run_success=True, layout_score=92)
    manifest = create_candidate_from_project(
        project_dir, templates_dir, description="my saved divider"
    )
    assert manifest.status == TemplateStatus.CANDIDATE
    assert manifest.layoutScore == 92
    assert manifest.simulationVerified is True
    # On-disk artifacts.
    cand = templates_dir / "candidates" / "my_vdiv"
    assert cand.is_dir()
    assert (cand / "manifest.json").is_file()
    assert (cand / "template.ir.json").is_file()


def test_create_candidate_from_project_rejects_missing_result(tmp_path: Path) -> None:
    """A project without result.json cannot become a candidate."""
    from ltagent.templates import (
        ERR_TEMPLATE_PROJECT_NO_RESULT,
        create_candidate_from_project,
    )

    project_dir = tmp_path / "projects" / "p1"
    project_dir.mkdir(parents=True)
    (project_dir / "circuit.ir.json").write_text(
        json.dumps(
            {
                "schemaVersion": "0.1",
                "name": "no_result",
                "topology": "voltage_divider",
                "nodes": ["in", "out", "0"],
                "components": [],
                "analysis": [{"kind": "op"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(TemplateError) as exc:
        create_candidate_from_project(project_dir, tmp_path / "templates")
    assert exc.value.code == ERR_TEMPLATE_PROJECT_NO_RESULT


def test_create_candidate_from_project_rejects_sim_failure(tmp_path: Path) -> None:
    """A failed simulation cannot become a candidate (gate 1 from Phase 9)."""
    from ltagent.templates import (
        ERR_TEMPLATE_SIM_NOT_VERIFIED,
        create_candidate_from_project,
    )

    project_dir = tmp_path / "projects" / "p1"
    ir = {
        "schemaVersion": "0.1",
        "name": "bad_sim",
        "topology": "voltage_divider",
        "nodes": ["in", "out", "0"],
        "components": [
            {"id": "Vin", "kind": "voltage_source", "spicePrefix": "V",
             "nodes": ["in", "0"], "value": "DC 12", "role": "input_source"},
            {"id": "R1", "kind": "resistor", "spicePrefix": "R",
             "nodes": ["in", "out"], "value": "1k", "role": "series_resistor"},
            {"id": "R2", "kind": "resistor", "spicePrefix": "R",
             "nodes": ["out", "0"], "value": "1k", "role": "shunt_resistor"},
        ],
        "analysis": [{"kind": "op"}],
    }
    _write_project(project_dir, ir=ir, run_success=False, layout_score=92)
    with pytest.raises(TemplateError) as exc:
        create_candidate_from_project(project_dir, tmp_path / "templates")
    assert exc.value.code == ERR_TEMPLATE_SIM_NOT_VERIFIED


def test_create_candidate_from_project_rejects_missing_layout(tmp_path: Path) -> None:
    """A project without a layout score cannot become a candidate (gate 2)."""
    from ltagent.templates import (
        ERR_TEMPLATE_LAYOUT_MISSING,
        create_candidate_from_project,
    )

    project_dir = tmp_path / "projects" / "p1"
    ir = {
        "schemaVersion": "0.1",
        "name": "no_layout",
        "topology": "voltage_divider",
        "nodes": ["in", "out", "0"],
        "components": [
            {"id": "Vin", "kind": "voltage_source", "spicePrefix": "V",
             "nodes": ["in", "0"], "value": "DC 12", "role": "input_source"},
            {"id": "R1", "kind": "resistor", "spicePrefix": "R",
             "nodes": ["in", "out"], "value": "1k", "role": "series_resistor"},
            {"id": "R2", "kind": "resistor", "spicePrefix": "R",
             "nodes": ["out", "0"], "value": "1k", "role": "shunt_resistor"},
        ],
        "analysis": [{"kind": "op"}],
    }
    _write_project(project_dir, ir=ir, run_success=True, layout_score=None)
    with pytest.raises(TemplateError) as exc:
        create_candidate_from_project(project_dir, tmp_path / "templates")
    assert exc.value.code == ERR_TEMPLATE_LAYOUT_MISSING


def test_create_candidate_from_project_rejects_duplicate_id(tmp_path: Path) -> None:
    """Re-creating a candidate for the same id is refused."""
    from ltagent.templates import (
        ERR_TEMPLATE_DUPLICATE,
        create_candidate_from_project,
    )

    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    project_dir = tmp_path / "projects" / "p1"
    ir = {
        "schemaVersion": "0.1",
        "name": "dup_id",
        "topology": "voltage_divider",
        "nodes": ["in", "out", "0"],
        "components": [
            {"id": "Vin", "kind": "voltage_source", "spicePrefix": "V",
             "nodes": ["in", "0"], "value": "DC 12", "role": "input_source"},
            {"id": "R1", "kind": "resistor", "spicePrefix": "R",
             "nodes": ["in", "out"], "value": "1k", "role": "series_resistor"},
            {"id": "R2", "kind": "resistor", "spicePrefix": "R",
             "nodes": ["out", "0"], "value": "1k", "role": "shunt_resistor"},
        ],
        "analysis": [{"kind": "op"}],
    }
    _write_project(project_dir, ir=ir)
    create_candidate_from_project(project_dir, templates_dir)
    with pytest.raises(TemplateError) as exc:
        create_candidate_from_project(project_dir, templates_dir)
    assert exc.value.code == ERR_TEMPLATE_DUPLICATE


def test_create_candidate_from_project_rejects_missing_dir(tmp_path: Path) -> None:
    from ltagent.templates import (
        ERR_TEMPLATE_PROJECT_INVALID,
        create_candidate_from_project,
    )

    with pytest.raises(TemplateError) as exc:
        create_candidate_from_project(
            tmp_path / "does_not_exist", tmp_path / "templates"
        )
    assert exc.value.code == ERR_TEMPLATE_PROJECT_INVALID


def test_create_candidate_from_project_evaluates_cleanly(tmp_path: Path) -> None:
    """The bridge + Phase 9 evaluator work together end-to-end."""
    from ltagent.evaluator import (
        PromotionDecision,
        evaluate_candidate,
    )
    from ltagent.templates import create_candidate_from_project

    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    project_dir = tmp_path / "projects" / "p1"
    ir = {
        "schemaVersion": "0.1",
        "name": "vdiv_bridge",
        "topology": "voltage_divider",
        "description": "Reusable voltage divider",
        "nodes": ["in", "out", "0"],
        "components": [
            {"id": "Vin", "kind": "voltage_source", "spicePrefix": "V",
             "nodes": ["in", "0"], "value": "DC 12", "role": "input_source"},
            {"id": "R1", "kind": "resistor", "spicePrefix": "R",
             "nodes": ["in", "out"], "value": "1k", "role": "series_resistor"},
            {"id": "R2", "kind": "resistor", "spicePrefix": "R",
             "nodes": ["out", "0"], "value": "1k", "role": "shunt_resistor"},
        ],
        "analysis": [{"kind": "op"}],
    }
    _write_project(project_dir, ir=ir, layout_score=92)
    # Use the user-requested tag so the candidate picks up the +3 score.
    manifest = create_candidate_from_project(
        project_dir, templates_dir, tags=("user-requested",)
    )
    ev = evaluate_candidate(
        templates_dir, manifest.templateId, status=TemplateStatus.CANDIDATE
    )
    # No official exists for this topology in the empty templates
    # dir, so the candidate earns the full +2 NO_SIMILAR bonus plus
    # the topology bonus and the simulation/layout bonuses.
    assert ev.decision == PromotionDecision.OFFICIAL
    assert ev.promotion_eligible is True
