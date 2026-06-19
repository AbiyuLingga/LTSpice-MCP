"""Tests for :mod:`ltagent.mcp_live_tools` (Agent 6, Phase 13).

These tests cover the public surface defined in
``ltspice_file_based_live_editing_math_plan.md`` §11 — MCP live
editing tools — without depending on :mod:`ltagent.live` or
:mod:`ltagent.math_core` (which other agents are building in
parallel). The tests:

* verify that invalid input is rejected with a stable error code,
* verify that every tool payload is JSON-serializable,
* verify that path traversal is rejected for every path-bearing tool,
* verify the live-editing tools return the structured
  ``LIVE_MODULE_UNAVAILABLE`` / ``LIVE_METHOD_MISSING`` payload when
  the live module does not expose the expected entry point,
* verify that the math tools produce correct ideal values via the
  built-in mini library, and
* verify that a monkey-patched fake live / math_core module is
  accepted by the dispatch helper.
"""

from __future__ import annotations

import json
import math
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

import ltagent.mcp_live_tools as ml
from ltagent import mcp_live_tools


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An isolated cwd with empty ``projects/`` and ``templates/`` dirs."""
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "projects").mkdir()
    (cwd / "templates").mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    yield cwd


@pytest.fixture()
def live_module_mock(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Inject a fake ``ltagent.live`` module and return it for inspection."""
    fake = types.ModuleType("ltagent.live")

    def fake_apply(project_dir, op, **kwargs):
        return {
            "applied": True,
            "op": op["op"],
            "projectDir": str(project_dir),
            "autoSnapshot": kwargs.get("auto_snapshot", True),
        }

    def fake_snapshot(project_dir, reason, **kwargs):
        return {
            "snapshotId": "001_initial",
            "reason": reason,
            "projectDir": str(project_dir),
        }

    def fake_restore(project_dir, snapshot_id, **kwargs):
        return {
            "restored": True,
            "snapshotId": snapshot_id,
            "projectDir": str(project_dir),
        }

    def fake_run_and_verify(project_dir, **kwargs):
        return {
            "passed": True,
            "projectDir": str(project_dir),
            "measurements": {"fc": 994.7},
            "checks": [{"name": "cutoff", "passed": True}],
        }

    fake.apply_operation = fake_apply
    fake.snapshot = fake_snapshot
    fake.restore = fake_restore
    fake.run_and_verify = fake_run_and_verify
    monkeypatch.setattr(mcp_live_tools, "_LIVE_MODULE", fake)
    return fake


@pytest.fixture()
def math_core_mock(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Inject a fake ``ltagent.math_core`` module and return it for inspection."""
    fake = types.ModuleType("ltagent.math_core")

    def fake_calculate(topology, parameters):
        return {
            "topology": topology,
            "idealValues": {"R": {"value": 42.0, "unit": "ohm"}},
            "formulas": [{"name": "mock_formula", "expression": "mock = 42"}],
            "assumptions": ["mocked"],
            "source": "math_core",
        }

    def fake_explain(topology, parameters):
        return {
            "topology": topology,
            "description": "mocked topology",
            "formulas": [{"name": "mock_formula", "expression": "mock = 42"}],
            "assumptions": ["mocked"],
            "source": "math_core",
        }

    fake.calculate = fake_calculate
    fake.explain = fake_explain
    monkeypatch.setattr(mcp_live_tools, "_MATH_CORE_MODULE", fake)
    return fake


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_payload(payload: dict) -> bool:
    assert payload.get("success") is True, payload
    for key in ("command", "message", "data", "warnings", "errors"):
        assert key in payload, f"missing {key!r} in {payload}"
    assert isinstance(payload["warnings"], list)
    assert isinstance(payload["errors"], list)
    return True


def _err_payload(payload: dict, *, code: str | None = None) -> bool:
    assert payload.get("success") is False, payload
    assert payload.get("errors"), payload
    if code is not None:
        codes = [e.get("code") for e in payload["errors"]]
        assert code in codes, f"expected code {code!r} in {codes}"
    return True


def _assert_jsonable(payload: dict) -> None:
    json.dumps(payload)


# ---------------------------------------------------------------------------
# Public surface: every tool exists, is callable, returns the right shape
# ---------------------------------------------------------------------------


EXPECTED_TOOL_NAMES = (
    "tool_live_open_project",
    "tool_live_inspect_project",
    "tool_live_apply_edit",
    "tool_live_snapshot",
    "tool_live_restore_snapshot",
    "tool_live_run_and_verify",
    "tool_calculate_circuit",
    "tool_explain_calculation",
)


def test_all_expected_tool_functions_exposed() -> None:
    for name in EXPECTED_TOOL_NAMES:
        assert hasattr(ml, name), f"missing tool: {name}"
        assert callable(getattr(ml, name)), f"{name} is not callable"


def test_no_dangerous_keywords_in_module() -> None:
    """The module must not expose a generic shell, execute_python, or
    allow_outside_workspace escape hatch on the public tool surface."""
    import inspect

    for name in EXPECTED_TOOL_NAMES:
        fn = getattr(ml, name)
        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())
        for forbidden in ("run_shell", "execute_python", "allow_outside_workspace"):
            assert forbidden not in params, (
                f"{name} exposes forbidden parameter {forbidden!r}"
            )


def test_no_subprocess_or_shell_invocation_in_module() -> None:
    """The module must not call subprocess / os.system / shell=True.

    We strip the docstring first to avoid false positives on the
    word "shell" or "shell=True" appearing in prose.
    """
    src = Path(ml.__file__).read_text(encoding="utf-8")
    stripped = src.split('"""', 2)
    code_only = "".join(stripped[::2]) if len(stripped) >= 3 else src
    assert "shell=True" not in code_only
    assert "os.system" not in code_only
    assert "subprocess.run" not in code_only
    assert "subprocess.call" not in code_only
    assert "subprocess.Popen" not in code_only


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda: ml.tool_live_open_project("../etc"),
        lambda: ml.tool_live_open_project("/etc/passwd"),
        lambda: ml.tool_live_open_project("foo/../bar"),
        lambda: ml.tool_live_open_project(".hidden"),
        lambda: ml.tool_live_inspect_project("../etc"),
        lambda: ml.tool_live_inspect_project("/etc"),
        lambda: ml.tool_live_apply_edit("../etc", {"op": "noop", "args": {}}),
        lambda: ml.tool_live_apply_edit("/etc", {"op": "noop", "args": {}}),
        lambda: ml.tool_live_snapshot("../etc"),
        lambda: ml.tool_live_restore_snapshot("../etc", "001"),
        lambda: ml.tool_live_run_and_verify("../etc"),
    ],
)
def test_path_traversal_rejected(workspace: Path, call) -> None:
    result = call()
    _err_payload(result, code="IDENTIFIER_INVALID")


def test_snapshot_id_with_path_separator_rejected(workspace: Path) -> None:
    result = ml.tool_live_restore_snapshot("rc1k", "../etc")
    _err_payload(result, code="INVALID_SNAPSHOT_ID")


def test_snapshot_id_with_subdir_rejected(workspace: Path) -> None:
    result = ml.tool_live_restore_snapshot("rc1k", "subdir/foo")
    _err_payload(result, code="INVALID_SNAPSHOT_ID")


def test_snapshot_id_with_dotdot_segment_rejected(workspace: Path) -> None:
    result = ml.tool_live_restore_snapshot("rc1k", "..")
    _err_payload(result, code="INVALID_SNAPSHOT_ID")


# ---------------------------------------------------------------------------
# Invalid input -> success=False with stable code
# ---------------------------------------------------------------------------


def test_open_project_missing_id() -> None:
    result = ml.tool_live_open_project("")
    _err_payload(result, code="MISSING_PARAM")
    _assert_jsonable(result)


def test_open_project_non_string_id() -> None:
    result = ml.tool_live_open_project(None)  # type: ignore[arg-type]
    _err_payload(result, code="MISSING_PARAM")


def test_inspect_project_missing_id() -> None:
    result = ml.tool_live_inspect_project("")
    _err_payload(result, code="MISSING_PARAM")
    _assert_jsonable(result)


def test_apply_edit_non_dict_operation() -> None:
    result = ml.tool_live_apply_edit("rc1k", None)
    _err_payload(result, code="INVALID_OPERATION")
    _assert_jsonable(result)


def test_apply_edit_operation_without_op_field() -> None:
    result = ml.tool_live_apply_edit("rc1k", {"args": {}})
    _err_payload(result, code="INVALID_OPERATION")
    _assert_jsonable(result)


def test_apply_edit_operation_args_not_dict() -> None:
    result = ml.tool_live_apply_edit("rc1k", {"op": "x", "args": "bad"})
    _err_payload(result, code="INVALID_OPERATION")
    _assert_jsonable(result)


def test_snapshot_reason_not_string(workspace: Path) -> None:
    (workspace / "projects" / "rc1k").mkdir()
    result = ml.tool_live_snapshot("rc1k", reason=123)  # type: ignore[arg-type]
    _err_payload(result, code="INVALID_INPUT")


def test_restore_snapshot_missing_id(workspace: Path) -> None:
    (workspace / "projects" / "rc1k").mkdir()
    result = ml.tool_live_restore_snapshot("rc1k", "")
    _err_payload(result, code="MISSING_PARAM")
    _assert_jsonable(result)


def test_run_and_verify_missing_id() -> None:
    result = ml.tool_live_run_and_verify("")
    _err_payload(result, code="MISSING_PARAM")
    _assert_jsonable(result)


def test_calculate_circuit_missing_topology() -> None:
    result = ml.tool_calculate_circuit("", {"fc": 1000})
    _err_payload(result, code="MISSING_PARAM")
    _assert_jsonable(result)


def test_calculate_circuit_non_dict_params() -> None:
    result = ml.tool_calculate_circuit("rc_lowpass", None)
    _err_payload(result, code="INVALID_INPUT")
    _assert_jsonable(result)


def test_calculate_circuit_unknown_topology() -> None:
    result = ml.tool_calculate_circuit("not_a_real_topology", {"foo": 1})
    _err_payload(result, code="INVALID_TOPOLOGY")
    _assert_jsonable(result)


def test_calculate_circuit_project_id_traversal() -> None:
    result = ml.tool_calculate_circuit("rc_lowpass", {"fc": 1}, project_id="../etc")
    _err_payload(result, code="IDENTIFIER_INVALID")
    _assert_jsonable(result)


def test_explain_calculation_missing_topology() -> None:
    result = ml.tool_explain_calculation("")
    _err_payload(result, code="MISSING_PARAM")
    _assert_jsonable(result)


def test_explain_calculation_non_dict_params() -> None:
    result = ml.tool_explain_calculation("rc_lowpass", "not-a-dict")  # type: ignore[arg-type]
    _err_payload(result, code="INVALID_INPUT")
    _assert_jsonable(result)


# ---------------------------------------------------------------------------
# Output is always JSON-serializable
# ---------------------------------------------------------------------------


def test_all_invalid_payloads_are_jsonable() -> None:
    """Run a battery of invalid inputs and assert each payload is
    JSON-serializable. This is the contract MCP clients rely on."""
    cases = [
        lambda: ml.tool_live_open_project(""),
        lambda: ml.tool_live_open_project("../etc"),
        lambda: ml.tool_live_inspect_project(""),
        lambda: ml.tool_live_apply_edit("", None),
        lambda: ml.tool_live_apply_edit("rc1k", {"op": ""}),
        lambda: ml.tool_live_snapshot(""),
        lambda: ml.tool_live_snapshot("rc1k", reason={"not": "a string"}),
        lambda: ml.tool_live_restore_snapshot("", ""),
        lambda: ml.tool_live_restore_snapshot("rc1k", "../foo"),
        lambda: ml.tool_live_run_and_verify(""),
        lambda: ml.tool_calculate_circuit("", {}),
        lambda: ml.tool_calculate_circuit("rc_lowpass", None),
        lambda: ml.tool_calculate_circuit("rc_lowpass", {"fc": "notanumber"}),
        lambda: ml.tool_calculate_circuit("not_a_real_topology", {}),
        lambda: ml.tool_explain_calculation(""),
        lambda: ml.tool_explain_calculation("rc_lowpass", "not-a-dict"),
    ]
    for case in cases:
        payload = case()
        _assert_jsonable(payload)
        assert payload["success"] is False, payload
        assert payload["errors"], payload


# ---------------------------------------------------------------------------
# Open / inspect (file-based; work without the live module)
# ---------------------------------------------------------------------------


def test_open_project_success(workspace: Path) -> None:
    proj = workspace / "projects" / "rc1k"
    proj.mkdir()
    (proj / "metadata.json").write_text(
        json.dumps({"projectId": "rc1k", "topology": "rc_lowpass"}), encoding="utf-8"
    )
    (proj / "circuit.graph.json").write_text("{}", encoding="utf-8")

    result = ml.tool_live_open_project("rc1k")
    _ok_payload(result)
    assert result["data"]["projectId"] == "rc1k"
    assert result["data"]["isLiveProject"] is True
    assert result["data"]["metadata"]["topology"] == "rc_lowpass"
    _assert_jsonable(result)


def test_open_project_missing_metadata_still_succeeds(workspace: Path) -> None:
    (workspace / "projects" / "rc1k").mkdir()
    result = ml.tool_live_open_project("rc1k")
    _ok_payload(result)
    assert result["data"]["isLiveProject"] is False
    assert result["data"]["metadata"] is None
    # The plan §10.2 contract: missing graph should surface a warning.
    assert any(w.get("code") == "LIVE_GRAPH_MISSING" for w in result["warnings"])
    _assert_jsonable(result)


def test_open_project_does_not_exist(workspace: Path) -> None:
    result = ml.tool_live_open_project("ghost")
    _err_payload(result)
    # safe_resolve_under returns PATH_NOT_FOUND; PROJECT_NOT_FOUND is
    # reserved for cases where we *have* a project dir but it is
    # missing the expected artefact. Accept either for now.
    codes = {e.get("code") for e in result["errors"]}
    assert codes <= {"PATH_NOT_FOUND", "PROJECT_NOT_FOUND"}
    _assert_jsonable(result)


def test_inspect_project_success(workspace: Path) -> None:
    proj = workspace / "projects" / "rc1k"
    proj.mkdir()
    (proj / "circuit.graph.json").write_text("{}", encoding="utf-8")
    (proj / "circuit.ir.json").write_text("{}", encoding="utf-8")
    (proj / ".snapshots").mkdir()
    (proj / ".snapshots" / "001_initial").mkdir()
    (proj / ".snapshots" / "002_before_edit").mkdir()

    result = ml.tool_live_inspect_project("rc1k")
    _ok_payload(result)
    assert result["data"]["hasGraph"] is True
    assert result["data"]["hasIR"] is True
    assert result["data"]["snapshots"] == ["001_initial", "002_before_edit"]
    _assert_jsonable(result)


def test_inspect_project_does_not_exist(workspace: Path) -> None:
    result = ml.tool_live_inspect_project("ghost")
    _err_payload(result)
    codes = {e.get("code") for e in result["errors"]}
    assert codes <= {"PATH_NOT_FOUND", "PROJECT_NOT_FOUND"}
    _assert_jsonable(result)


# ---------------------------------------------------------------------------
# Live backend: missing -> structured error; monkey-patched -> success
# ---------------------------------------------------------------------------


def test_apply_edit_reports_live_module_unavailable_when_backend_missing(
    workspace: Path,
) -> None:
    # Force the module-unavailable path even if another agent's live
    # package has landed.
    import ltagent.mcp_live_tools as srv
    saved = srv._LIVE_MODULE
    srv._LIVE_MODULE = None
    try:
        (workspace / "projects" / "rc1k").mkdir()
        result = ml.tool_live_apply_edit(
            "rc1k", {"op": "set_component_value", "args": {"componentId": "R1"}}
        )
        _err_payload(result, code="LIVE_MODULE_UNAVAILABLE")
        _assert_jsonable(result)
    finally:
        srv._LIVE_MODULE = saved


def test_apply_edit_reports_live_method_missing_when_entrypoint_absent(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = types.ModuleType("ltagent.live")
    # deliberately no apply_operation
    monkeypatch.setattr(mcp_live_tools, "_LIVE_MODULE", fake)
    (workspace / "projects" / "rc1k").mkdir()
    result = ml.tool_live_apply_edit(
        "rc1k", {"op": "set_component_value", "args": {"componentId": "R1"}}
    )
    _err_payload(result, code="LIVE_METHOD_MISSING")
    _assert_jsonable(result)


def test_apply_edit_uses_live_module(workspace: Path, live_module_mock) -> None:
    (workspace / "projects" / "rc1k").mkdir()
    result = ml.tool_live_apply_edit(
        "rc1k",
        {"op": "set_component_value", "args": {"componentId": "R1", "value": "1.6k"}},
        auto_snapshot=False,
    )
    _ok_payload(result)
    assert result["data"]["applied"] is True
    assert result["data"]["op"] == "set_component_value"
    assert result["data"]["autoSnapshot"] is False
    _assert_jsonable(result)


def test_snapshot_uses_live_module(workspace: Path, live_module_mock) -> None:
    (workspace / "projects" / "rc1k").mkdir()
    result = ml.tool_live_snapshot("rc1k", reason="manual checkpoint")
    _ok_payload(result)
    assert result["data"]["snapshotId"] == "001_initial"
    assert result["data"]["reason"] == "manual checkpoint"
    _assert_jsonable(result)


def test_restore_uses_live_module(workspace: Path, live_module_mock) -> None:
    (workspace / "projects" / "rc1k").mkdir()
    result = ml.tool_live_restore_snapshot("rc1k", "002_before")
    _ok_payload(result)
    assert result["data"]["restored"] is True
    assert result["data"]["snapshotId"] == "002_before"
    _assert_jsonable(result)


def test_run_and_verify_uses_live_module(workspace: Path, live_module_mock) -> None:
    (workspace / "projects" / "rc1k").mkdir()
    result = ml.tool_live_run_and_verify("rc1k")
    _ok_payload(result)
    assert result["data"]["passed"] is True
    assert result["data"]["measurements"]["fc"] == 994.7
    _assert_jsonable(result)


def test_apply_edit_propagates_value_error_from_live(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = types.ModuleType("ltagent.live")

    def boom(project_dir, op, **kwargs):
        raise ValueError("bad op")

    fake.apply_operation = boom
    monkeypatch.setattr(mcp_live_tools, "_LIVE_MODULE", fake)
    (workspace / "projects" / "rc1k").mkdir()
    result = ml.tool_live_apply_edit(
        "rc1k", {"op": "set_component_value", "args": {"componentId": "R1"}}
    )
    _err_payload(result, code="EDIT_OP_FAILED")
    assert "bad op" in result["errors"][0]["detail"]


# ---------------------------------------------------------------------------
# Math tools: built-in mini library + math_core dispatch
# ---------------------------------------------------------------------------


def test_calculate_circuit_rc_lowpass_solves_r(workspace: Path) -> None:
    result = ml.tool_calculate_circuit(
        "rc_lowpass", {"fc": 1000, "C": "100nF"}
    )
    _ok_payload(result)
    r = result["data"]["idealValues"]["R"]
    expected = 1.0 / (2.0 * math.pi * 1000.0 * 1e-7)
    assert math.isclose(r["value"], expected, rel_tol=1e-9)
    assert r["unit"] == "ohm"
    assert result["data"]["source"] == "builtin_fallback"
    _assert_jsonable(result)


def test_calculate_circuit_rc_lowpass_solves_fc(workspace: Path) -> None:
    result = ml.tool_calculate_circuit(
        "rc_lowpass", {"R": 1600, "C": "100nF"}
    )
    _ok_payload(result)
    fc = result["data"]["idealValues"]["fc"]
    expected = 1.0 / (2.0 * math.pi * 1600.0 * 1e-7)
    assert math.isclose(fc["value"], expected, rel_tol=1e-9)
    _assert_jsonable(result)


def test_calculate_circuit_rc_highpass_solves_r(workspace: Path) -> None:
    result = ml.tool_calculate_circuit(
        "rc_highpass", {"fc": 500, "C": "1uF"}
    )
    _ok_payload(result)
    r = result["data"]["idealValues"]["R"]
    expected = 1.0 / (2.0 * math.pi * 500.0 * 1e-6)
    assert math.isclose(r["value"], expected, rel_tol=1e-9)
    _assert_jsonable(result)


def test_calculate_circuit_voltage_divider(workspace: Path) -> None:
    result = ml.tool_calculate_circuit(
        "voltage_divider", {"vin": 12, "vout": 5, "r2": 1000}
    )
    _ok_payload(result)
    r1 = result["data"]["idealValues"]["r1"]
    # R1 = R2 * (Vin - Vout) / Vout = 1000 * 7 / 5 = 1400
    assert math.isclose(r1["value"], 1400.0, rel_tol=1e-9)
    _assert_jsonable(result)


def test_calculate_circuit_noninv_opamp(workspace: Path) -> None:
    result = ml.tool_calculate_circuit(
        "noninv_opamp", {"gain": 10, "rg": 1000}
    )
    _ok_payload(result)
    rf = result["data"]["idealValues"]["rf"]
    # Rf = (Av - 1) * Rg = 9 * 1000 = 9000
    assert math.isclose(rf["value"], 9000.0, rel_tol=1e-9)
    _assert_jsonable(result)


def test_calculate_circuit_inverting_opamp(workspace: Path) -> None:
    result = ml.tool_calculate_circuit(
        "inverting_opamp", {"gain": -5, "rin": 1000}
    )
    _ok_payload(result)
    rf = result["data"]["idealValues"]["rf"]
    # Rf = |Av| * Rin = 5 * 1000 = 5000
    assert math.isclose(rf["value"], 5000.0, rel_tol=1e-9)
    _assert_jsonable(result)


def test_calculate_circuit_led_resistor(workspace: Path) -> None:
    result = ml.tool_calculate_circuit(
        "led_resistor", {"vsupply": 5, "vf": 2, "iled": "20mA"}
    )
    _ok_payload(result)
    r = result["data"]["idealValues"]["R"]
    p = result["data"]["idealValues"]["P_R"]
    # R = (5 - 2) / 0.02 = 150
    assert math.isclose(r["value"], 150.0, rel_tol=1e-9)
    # P = I^2 * R = 0.02^2 * 150 = 0.06
    assert math.isclose(p["value"], 0.06, rel_tol=1e-9)
    _assert_jsonable(result)


def test_calculate_circuit_insufficient_parameters(workspace: Path) -> None:
    result = ml.tool_calculate_circuit("rc_lowpass", {"fc": 1000})
    _err_payload(result, code="CALCULATION_FAILED")
    _assert_jsonable(result)


def test_calculate_circuit_physical_constraint_violated(workspace: Path) -> None:
    # noninv_opamp gain must be > 1
    result = ml.tool_calculate_circuit(
        "noninv_opamp", {"gain": 0.5, "rg": 1000}
    )
    _err_payload(result, code="CALCULATION_FAILED")
    assert "gain > 1" in result["errors"][0]["detail"]
    _assert_jsonable(result)


def test_calculate_circuit_uses_math_core_when_available(
    workspace: Path, math_core_mock
) -> None:
    result = ml.tool_calculate_circuit("rc_lowpass", {"fc": 1000, "C": "100nF"})
    _ok_payload(result)
    assert result["data"]["source"] == "math_core"
    assert result["data"]["idealValues"]["R"]["value"] == 42.0
    _assert_jsonable(result)


def test_explain_calculation_rc_lowpass(workspace: Path) -> None:
    result = ml.tool_explain_calculation("rc_lowpass")
    _ok_payload(result)
    assert result["data"]["topology"] == "rc_lowpass"
    assert result["data"]["formulas"][0]["expression"] == "fc = 1 / (2*pi*R*C)"
    # assumptions is a list[str]; check substring on any entry
    assumptions = result["data"]["assumptions"]
    assert any("ideal capacitor" in a for a in assumptions), assumptions
    _assert_jsonable(result)


def test_explain_calculation_unknown_topology(workspace: Path) -> None:
    result = ml.tool_explain_calculation("not_a_real_topology")
    _err_payload(result, code="INVALID_TOPOLOGY")
    _assert_jsonable(result)


def test_explain_calculation_uses_math_core_when_available(
    workspace: Path, math_core_mock
) -> None:
    result = ml.tool_explain_calculation("rc_lowpass")
    _ok_payload(result)
    assert result["data"]["source"] == "math_core"
    assert result["data"]["description"] == "mocked topology"
    _assert_jsonable(result)


def test_supported_builtin_topologies_lists_all_known() -> None:
    supported = set(ml.supported_builtin_topologies())
    assert {
        "voltage_divider",
        "rc_lowpass",
        "rc_highpass",
        "noninv_opamp",
        "inverting_opamp",
        "led_resistor",
    }.issubset(supported)


# ---------------------------------------------------------------------------
# Introspection helpers
# ---------------------------------------------------------------------------


def test_live_module_available_returns_bool() -> None:
    assert isinstance(ml.live_module_available(), bool)


def test_math_core_available_returns_bool() -> None:
    assert isinstance(ml.math_core_available(), bool)


# ---------------------------------------------------------------------------
# Integration tests: success payload with file-based project + math-core
# ---------------------------------------------------------------------------


def test_open_then_calculate_with_project_id(
    workspace: Path, math_core_mock
) -> None:
    proj = workspace / "projects" / "rc1k"
    proj.mkdir()
    (proj / "metadata.json").write_text(
        json.dumps({"projectId": "rc1k", "topology": "rc_lowpass"}), encoding="utf-8"
    )

    # 1. open
    open_result = ml.tool_live_open_project("rc1k")
    _ok_payload(open_result)
    # 2. calculate, with project_id set (still a pure-math call)
    calc = ml.tool_calculate_circuit(
        "rc_lowpass", {"fc": 1000, "C": "100nF"}, project_id="rc1k"
    )
    _ok_payload(calc)
    # math_core is the mock, which ignores the inputs
    assert calc["data"]["source"] == "math_core"
    _assert_jsonable(open_result)
    _assert_jsonable(calc)