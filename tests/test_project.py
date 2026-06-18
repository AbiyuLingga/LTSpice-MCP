"""Unit tests for :mod:`ltagent.project` (Phase 7 orchestrator).

The orchestrator is a pure-Python module that wires the existing
phase modules together. We exercise it through the public
:func:`create_project` function and assert on the
:class:`ProjectResult` shape, on the on-disk files, and on the JSON
contract that lands in ``result.json`` and ``metadata.json``.

No LTspice / Wine is invoked. Tests run quickly and deterministically.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from ltagent.config import (
    Config,
    LayoutConfig,
    LTSpiceConfig,
    RunnerConfig,
    TemplatesConfig,
    WorkspaceConfig,
)
from ltagent.ir import load_ir
from ltagent.project import (
    FILE_ASC,
    FILE_CIR,
    FILE_IR,
    FILE_METADATA,
    FILE_RESULT,
    PRJ_ERR_INVALID_IR,
    PRJ_ERR_TARGET_NOT_EMPTY,
    PRJ_WARN_LTSPICE_UNAVAILABLE,
    PRJ_WARN_RUN_SKIPPED_BY_CONFIG,
    PRJ_WARN_TEMPLATE_NOT_FOUND,
    ProjectResult,
    build_project_id,
    create_project,
)
from ltagent.templates import seed_default_templates

# --- helpers ---------------------------------------------------------------


def _default_config() -> Config:
    """Return a Config with no LTspice executable so runs become 'skipped'."""
    return Config(
        workspace=WorkspaceConfig(projects_dir="projects", templates_dir="templates"),
        ltspice=LTSpiceConfig(mode="wine", executable="", wine_command=""),
        runner=RunnerConfig(),
        layout=LayoutConfig(),
        templates=TemplatesConfig(),
    )


def _empty_templates_dir(td: Path) -> Path:
    """Create and return an empty templates dir under ``td``."""
    tdir = td / "templates"
    tdir.mkdir(parents=True, exist_ok=True)
    return tdir


def _seeded_templates_dir(td: Path) -> Path:
    """Create and return a templates dir with the 3 MVP default seeds."""
    tdir = td / "templates"
    tdir.mkdir(parents=True, exist_ok=True)
    seed_default_templates(tdir)
    return tdir


# --- build_project_id ------------------------------------------------------


def test_build_project_id_uses_iso_date_and_safe_name() -> None:
    pid = build_project_id("rc_lowpass_1khz", when=date(2026, 1, 2))
    assert pid == "2026-01-02_rc_lowpass_1khz"


def test_build_project_id_sanitises_unsafe_characters() -> None:
    pid = build_project_id("My Circuit!", when=date(2026, 1, 2))
    # '!' and space collapse to '_'; capitals are lowercased; trailing
    # '_' is stripped so the result is a clean path segment.
    assert pid == "2026-01-02_my_circuit"


def test_build_project_id_rejects_empty_name() -> None:
    with pytest.raises(ValueError):
        build_project_id("", when=date(2026, 1, 2))


def test_build_project_id_rejects_name_with_no_safe_chars() -> None:
    with pytest.raises(ValueError):
        build_project_id("!!!", when=date(2026, 1, 2))


# --- create_project: success path (no run) ---------------------------------


def test_create_project_writes_all_required_artifacts(
    tmp_path: Path, examples_dir: Path
) -> None:
    config = _default_config()
    tdir = _empty_templates_dir(tmp_path)
    target = tmp_path / "projects" / build_project_id("rc_lowpass_1khz", when=date(2026, 6, 18))
    ir_path = examples_dir / "rc_lowpass.ir.json"

    res = create_project(
        ir=ir_path,
        target=target,
        templates_dir=tdir,
        config=config,
        run_simulation=False,
        when=date(2026, 6, 18),
    )

    assert isinstance(res, ProjectResult)
    assert res.success
    assert res.errors == []
    # All 5 required files are present.
    assert res.ir_path.is_file()
    assert res.cir_path.is_file()
    assert res.asc_path.is_file()
    assert res.result_path.is_file()
    assert res.metadata_path.is_file()
    # Run was not requested, so log / raw are absent.
    assert res.log_path is None
    assert res.raw_path is None
    assert res.run_status == "not_requested"


def test_create_project_persists_validated_ir(
    tmp_path: Path, examples_dir: Path
) -> None:
    target = tmp_path / "p" / build_project_id("rc_lowpass_1khz", when=date(2026, 6, 18))
    res = create_project(
        ir=examples_dir / "rc_lowpass.ir.json",
        target=target,
        templates_dir=_empty_templates_dir(tmp_path),
        config=_default_config(),
        run_simulation=False,
        when=date(2026, 6, 18),
    )
    # The persisted IR must round-trip through the IR loader.
    reloaded = load_ir(res.ir_path)
    assert reloaded.name == "rc_lowpass_1khz"
    assert reloaded.topology == "rc_lowpass"


def test_create_project_netlist_contains_required_sections(
    tmp_path: Path, examples_dir: Path
) -> None:
    res = create_project(
        ir=examples_dir / "rc_lowpass.ir.json",
        target=tmp_path / "p" / "rc_lowpass_1khz",
        templates_dir=_empty_templates_dir(tmp_path),
        config=_default_config(),
        run_simulation=False,
        when=date(2026, 6, 18),
    )
    cir = res.cir_path.read_text(encoding="utf-8")
    assert "* Generated by" in cir
    assert "R1" in cir
    assert "C1" in cir
    assert ".end" in cir


def test_create_project_asc_contains_required_ltspice_lines(
    tmp_path: Path, examples_dir: Path
) -> None:
    res = create_project(
        ir=examples_dir / "rc_lowpass.ir.json",
        target=tmp_path / "p" / "rc_lowpass_1khz",
        templates_dir=_empty_templates_dir(tmp_path),
        config=_default_config(),
        run_simulation=False,
        when=date(2026, 6, 18),
    )
    asc = res.asc_path.read_text(encoding="utf-8")
    assert asc.startswith("Version 4")
    assert "SHEET" in asc
    assert "WIRE" in asc
    assert "SYMBOL" in asc
    assert "FLAG" in asc


def test_create_project_result_json_matches_contract(
    tmp_path: Path, examples_dir: Path
) -> None:
    res = create_project(
        ir=examples_dir / "rc_lowpass.ir.json",
        target=tmp_path / "p" / "rc_lowpass_1khz",
        templates_dir=_empty_templates_dir(tmp_path),
        config=_default_config(),
        run_simulation=False,
        when=date(2026, 6, 18),
    )
    payload = json.loads(res.result_path.read_text(encoding="utf-8"))
    # Top-level shape.
    for key in (
        "success",
        "projectId",
        "files",
        "run",
        "measurements",
        "assertions",
        "layout",
        "template",
        "warnings",
        "errors",
    ):
        assert key in payload, f"missing key {key!r} in result.json"
    # FileMap shape.
    for key in ("ir", "cir", "asc", "log", "raw", "result"):
        assert key in payload["files"], f"missing files.{key!r}"
    assert payload["files"]["ir"] == FILE_IR
    assert payload["files"]["cir"] == FILE_CIR
    assert payload["files"]["asc"] == FILE_ASC
    assert payload["files"]["result"] == FILE_RESULT
    # No run, so log/raw are null and run.attempted is false.
    assert payload["files"]["log"] is None
    assert payload["files"]["raw"] is None
    assert payload["run"]["attempted"] is False
    # Always-on assertions are present.
    assertion_names = {a["name"] for a in payload["assertions"]}
    assert "simulation_has_no_errors" in assertion_names
    assert "simulation_finished" in assertion_names


def test_create_project_metadata_json_matches_contract(
    tmp_path: Path, examples_dir: Path
) -> None:
    res = create_project(
        ir=examples_dir / "rc_lowpass.ir.json",
        target=tmp_path / "p" / "rc_lowpass_1khz",
        templates_dir=_empty_templates_dir(tmp_path),
        config=_default_config(),
        run_simulation=False,
        when=date(2026, 6, 18),
    )
    md = json.loads(res.metadata_path.read_text(encoding="utf-8"))
    for key in (
        "schemaVersion",
        "projectId",
        "name",
        "topology",
        "createdBy",
        "createdAt",
        "target",
        "files",
        "template",
        "layout",
        "run",
    ):
        assert key in md, f"missing key {key!r} in metadata.json"
    assert md["createdBy"] == "ltagent"
    assert md["files"]["metadata"] == FILE_METADATA
    assert md["run"]["status"] == "not_requested"
    assert md["template"]["used"] is None
    assert md["template"]["valueVariant"] is False
    # Layout score is computed for every supported topology.
    assert md["layout"]["score"] is not None
    assert 0 <= md["layout"]["score"] <= 100


# --- create_project: error paths -----------------------------------------


def test_create_project_rejects_existing_non_empty_target(
    tmp_path: Path, examples_dir: Path
) -> None:
    target = tmp_path / "p" / "rc_lowpass_1khz"
    target.mkdir(parents=True)
    (target / "junk.txt").write_text("do not clobber", encoding="utf-8")
    res = create_project(
        ir=examples_dir / "rc_lowpass.ir.json",
        target=target,
        templates_dir=_empty_templates_dir(tmp_path),
        config=_default_config(),
        run_simulation=False,
    )
    assert not res.success
    assert any(e["code"] == PRJ_ERR_TARGET_NOT_EMPTY for e in res.errors)
    # The existing file is preserved.
    assert (target / "junk.txt").read_text(encoding="utf-8") == "do not clobber"


def test_create_project_allows_overwriting_existing_empty_target(
    tmp_path: Path, examples_dir: Path
) -> None:
    target = tmp_path / "p" / "rc_lowpass_1khz"
    target.mkdir(parents=True)
    # Directory exists but is empty.
    res = create_project(
        ir=examples_dir / "rc_lowpass.ir.json",
        target=target,
        templates_dir=_empty_templates_dir(tmp_path),
        config=_default_config(),
        run_simulation=False,
    )
    assert res.success
    assert res.ir_path.is_file()


def test_create_project_rejects_invalid_ir_path(
    tmp_path: Path,
) -> None:
    res = create_project(
        ir=tmp_path / "does-not-exist.ir.json",
        target=tmp_path / "p" / "rc_lowpass_1khz",
        templates_dir=_empty_templates_dir(tmp_path),
        config=_default_config(),
        run_simulation=False,
    )
    assert not res.success
    assert any(e["code"] == PRJ_ERR_INVALID_IR for e in res.errors)


def test_create_project_rejects_invalid_ir_dict(
    tmp_path: Path,
) -> None:
    bad: dict[str, Any] = {
        "schemaVersion": "0.1",
        "name": "bad_circuit",
        # topology not in MVP_TOPOLOGIES
        "topology": "lumped_quadrupole",
        "nodes": ["in", "out", "0"],
        "components": [],
        "analysis": [{"kind": "op"}],
    }
    res = create_project(
        ir=bad,
        target=tmp_path / "p" / "bad_circuit",
        templates_dir=_empty_templates_dir(tmp_path),
        config=_default_config(),
        run_simulation=False,
    )
    assert not res.success
    assert any(e["code"] == PRJ_ERR_INVALID_IR for e in res.errors)


# --- create_project: simulation paths -------------------------------------


def test_create_project_without_simulation_adds_skipped_warning(
    tmp_path: Path, examples_dir: Path
) -> None:
    res = create_project(
        ir=examples_dir / "rc_lowpass.ir.json",
        target=tmp_path / "p" / "rc_lowpass_1khz",
        templates_dir=_empty_templates_dir(tmp_path),
        config=_default_config(),
        run_simulation=False,
    )
    codes = [w["code"] for w in res.warnings]
    assert PRJ_WARN_RUN_SKIPPED_BY_CONFIG in codes


def test_create_project_with_run_but_no_ltspice_reports_failure(
    tmp_path: Path, examples_dir: Path
) -> None:
    res = create_project(
        ir=examples_dir / "rc_lowpass.ir.json",
        target=tmp_path / "p" / "rc_lowpass_1khz",
        templates_dir=_empty_templates_dir(tmp_path),
        config=_default_config(),  # executable=""
        run_simulation=True,
    )
    # The project itself exists; the orchestrator did not crash.
    assert res.success
    assert res.ir_path.is_file()
    assert res.cir_path.is_file()
    assert res.asc_path.is_file()
    assert res.result_path.is_file()
    # But the run was attempted and the result reflects LTspice as
    # missing.
    assert res.run_status == "attempted"
    assert res.run_result is not None
    assert not res.run_result.success
    codes = [w["code"] for w in res.warnings]
    assert PRJ_WARN_LTSPICE_UNAVAILABLE in codes
    # The result.json also records the failure on the run block.
    rj = json.loads(res.result_path.read_text(encoding="utf-8"))
    assert rj["run"]["attempted"] is True
    assert rj["run"]["success"] is False


# --- create_project: template matching ------------------------------------


def test_create_project_matches_seeded_template(
    tmp_path: Path, examples_dir: Path
) -> None:
    tdir = _seeded_templates_dir(tmp_path)
    res = create_project(
        ir=examples_dir / "rc_lowpass.ir.json",
        target=tmp_path / "p" / "rc_lowpass_1khz",
        templates_dir=tdir,
        config=_default_config(),
        run_simulation=False,
        when=date(2026, 6, 18),
    )
    assert res.template_used == "rc_lowpass"
    assert res.template_value_variant is False
    md = json.loads(res.metadata_path.read_text(encoding="utf-8"))
    assert md["template"]["used"] == "rc_lowpass"


def test_create_project_records_no_template_when_library_empty(
    tmp_path: Path, examples_dir: Path
) -> None:
    res = create_project(
        ir=examples_dir / "rc_lowpass.ir.json",
        target=tmp_path / "p" / "rc_lowpass_1khz",
        templates_dir=_empty_templates_dir(tmp_path),
        config=_default_config(),
        run_simulation=False,
    )
    assert res.template_used is None
    codes = [w["code"] for w in res.warnings]
    # We do not currently emit TEMPLATE_NOT_FOUND from the orchestrator
    # itself (the CLI layer does); the project's warning list therefore
    # should not contain it. The contract is: orchestrator leaves the
    # template hint to the CLI.
    assert PRJ_WARN_TEMPLATE_NOT_FOUND not in codes


def test_create_project_accepts_circuit_ir_object(
    tmp_path: Path, examples_dir: Path
) -> None:
    """Passing a CircuitIR object directly must work as well as a path."""
    ir = load_ir(examples_dir / "rc_lowpass.ir.json")
    res = create_project(
        ir=ir,
        target=tmp_path / "p" / build_project_id(ir.name, when=date(2026, 6, 18)),
        templates_dir=_empty_templates_dir(tmp_path),
        config=_default_config(),
        run_simulation=False,
        when=date(2026, 6, 18),
    )
    assert res.success
    assert res.project_id == build_project_id(ir.name, when=date(2026, 6, 18))


def test_create_project_result_obj_has_layout_score(
    tmp_path: Path, examples_dir: Path
) -> None:
    res = create_project(
        ir=examples_dir / "voltage_divider.ir.json",
        target=tmp_path / "p" / "voltage_divider",
        templates_dir=_empty_templates_dir(tmp_path),
        config=_default_config(),
        run_simulation=False,
    )
    assert res.layout_score is not None
    # The first-party MVP topologies all render to a clean layout.
    assert res.layout_score >= 70
