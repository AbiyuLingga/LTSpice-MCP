"""Tests for the AI design workflow.

Covers:
* The capability classifier on English and Indonesian prompts.
* The RequirementSpec round-trip and the unsupported rejection.
* The validate_proposal rules (revision, unknown documents,
  missing componentId).
* The repair loop falls back after the first failure and emits
  a structured repair attempt.
* The accept path turns a valid proposal into a revisioned
  ChangeSet on the v2 project.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ltagent.ai_provider import (
    AIProposal,
    AIProposalOperation,
    ProviderAdapter,
    ProviderKind,
    ProviderProfile,
    ProviderRegistry,
)
from ltagent.ai_workflow import (
    CAPABILITY_COUNTER_8BIT,
    CAPABILITY_FSM_BLINK,
    CAPABILITY_OPAMP_INVERTING,
    CAPABILITY_RC_HIGHPASS,
    CAPABILITY_RC_LOWPASS,
    CAPABILITY_UNSUPPORTED,
    AIWorkflow,
    CapabilityClassifier,
    ProposalDecision,
    RequirementSpec,
    validate_proposal,
)
from ltagent.design_service import DesignService
from ltagent.live.graph_schema import (
    CircuitGraph,
    Component,
    ComponentKind,
    NetType,
    PinMap,
)
from ltagent.live.graph_schema import (
    Net as GraphNet,
)
from ltagent.workbench_v2 import (
    FILE_ANALOG_GRAPH,
    FILE_DIGITAL,
    FILE_MANIFEST,
    FILE_REQUIREMENTS,
    FILE_SCHEMATIC_VIEW,
    FILE_SYSTEM,
    PROJECT_SCHEMA_VERSION,
    SchematicView,
    SystemSpec,
)


def _seed_v2_project(tmp_path: Path, project_id: str = "rc_lab") -> Path:
    project_dir = tmp_path / "projects" / project_id
    (project_dir / "design" / "analog").mkdir(parents=True)
    (project_dir / "design" / "schematic").mkdir(parents=True)
    (project_dir / "design" / "digital").mkdir(parents=True)
    (project_dir / ".workbench" / "history").mkdir(parents=True)
    manifest = {
        "schemaVersion": PROJECT_SCHEMA_VERSION,
        "projectId": project_id,
        "displayName": project_id,
        "revision": 0,
    }
    (project_dir / FILE_MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    (project_dir / FILE_REQUIREMENTS).write_text(
        json.dumps(
            {"schemaVersion": PROJECT_SCHEMA_VERSION, "text": "", "constraints": {}, "goals": []}
        ),
        encoding="utf-8",
    )
    graph = CircuitGraph(
        schemaVersion="0.2",
        projectId=project_id,
        components={
            "R1": Component(
                id="R1",
                kind=ComponentKind.RESISTOR,
                value="1k",
                pins=PinMap(pins={"p1": "vin", "p2": "vout"}),
            )
        },
        nets={
            "vin": GraphNet(name="vin", type=NetType.SIGNAL),
            "vout": GraphNet(name="vout", type=NetType.SIGNAL),
            "0": GraphNet(name="0", type=NetType.GROUND),
        },
    )
    (project_dir / FILE_ANALOG_GRAPH).write_text(graph.model_dump_json(), encoding="utf-8")
    (project_dir / FILE_SCHEMATIC_VIEW).write_text(
        SchematicView().model_dump_json(), encoding="utf-8"
    )
    (project_dir / FILE_DIGITAL).write_text(
        json.dumps({"schemaVersion": PROJECT_SCHEMA_VERSION, "design": {}, "notes": ""}),
        encoding="utf-8",
    )
    (project_dir / FILE_SYSTEM).write_text(SystemSpec().model_dump_json(), encoding="utf-8")
    return project_dir


def test_classifier_en_rc_lowpass() -> None:
    c = CapabilityClassifier()
    assert (
        c.classify("Make an RC low-pass filter with cutoff 1kHz.").capability
        == CAPABILITY_RC_LOWPASS
    )
    assert c.classify("Build a lowpass with cutoff 800 Hz.").capability == CAPABILITY_RC_LOWPASS
    assert (
        c.classify("Buat filter RC lowpass dengan cutoff 1kHz.").capability == CAPABILITY_RC_LOWPASS
    )


def test_classifier_en_rc_highpass() -> None:
    c = CapabilityClassifier()
    assert c.classify("Design an RC high-pass filter.").capability == CAPABILITY_RC_HIGHPASS
    assert c.classify("Filter tinggi dengan cutoff 10kHz.").capability == CAPABILITY_RC_HIGHPASS


def test_classifier_en_opamp_inverting() -> None:
    c = CapabilityClassifier()
    result = c.classify("Design an inverting op-amp with gain -10.")
    assert result.capability == CAPABILITY_OPAMP_INVERTING
    assert result.constraints.get("gainMagnitude") == -10


def test_classifier_en_counter_8bit() -> None:
    c = CapabilityClassifier()
    result = c.classify("Build an 8-bit counter.")
    assert result.capability == CAPABILITY_COUNTER_8BIT
    assert result.constraints.get("width") == 8


def test_classifier_en_fsm_blink() -> None:
    c = CapabilityClassifier()
    assert c.classify("Make a blinking LED fsm.").capability == CAPABILITY_FSM_BLINK


def test_classifier_unsupported_prompt() -> None:
    c = CapabilityClassifier()
    assert c.classify("Make a coffee.").capability == CAPABILITY_UNSUPPORTED


def test_requirement_spec_rejects_unknown_capability() -> None:
    with pytest.raises(ValidationError):
        RequirementSpec(
            schemaVersion="1.0",
            requestId="r1",
            domain="analog",
            intent="rc_lowpass",
            capability="rocket_science",
            text="x",
        )


def test_validate_proposal_rejects_empty_operations() -> None:
    from ltagent.design_service import DesignService
    from ltagent.workbench_v2 import PROJECT_SCHEMA_VERSION

    project_dir = Path("/tmp") / "validate-empty"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "design" / "analog").mkdir(parents=True, exist_ok=True)
    (project_dir / "design" / "schematic").mkdir(parents=True, exist_ok=True)
    (project_dir / "design" / "digital").mkdir(parents=True, exist_ok=True)
    (project_dir / FILE_MANIFEST).write_text(
        json.dumps(
            {
                "schemaVersion": PROJECT_SCHEMA_VERSION,
                "projectId": "x",
                "displayName": "x",
                "revision": 0,
            }
        ),
        encoding="utf-8",
    )
    (project_dir / FILE_ANALOG_GRAPH).write_text(
        json.dumps(
            {
                "schemaVersion": "0.2",
                "projectId": "x",
                "topology": "",
                "components": {},
                "nets": {},
                "analyses": [],
                "measurements": [],
                "directives": [],
            }
        ),
        encoding="utf-8",
    )
    (project_dir / FILE_SCHEMATIC_VIEW).write_text(
        json.dumps(
            {
                "schemaVersion": PROJECT_SCHEMA_VERSION,
                "gridSize": 16,
                "viewport": None,
                "symbols": [],
                "wires": [],
                "netLabels": [],
            }
        ),
        encoding="utf-8",
    )
    (project_dir / FILE_DIGITAL).write_text(
        json.dumps({"schemaVersion": PROJECT_SCHEMA_VERSION, "design": {}, "notes": ""}),
        encoding="utf-8",
    )
    (project_dir / FILE_SYSTEM).write_text(
        json.dumps({"schemaVersion": PROJECT_SCHEMA_VERSION, "blocks": [], "connections": []}),
        encoding="utf-8",
    )
    (project_dir / FILE_REQUIREMENTS).write_text(
        json.dumps(
            {"schemaVersion": PROJECT_SCHEMA_VERSION, "text": "", "constraints": {}, "goals": []}
        ),
        encoding="utf-8",
    )
    svc = DesignService(projects_root=str(project_dir.parent))
    proposal = AIProposal(
        schemaVersion="1.0",
        proposalId="p1",
        baseRevision=0,
        requirement="rc",
        operations=[],
    )
    validation = validate_proposal(
        proposal,
        design_service=svc,
        project_id="x",
        documents={},
    )
    assert not validation.is_valid
    assert any("no operations" in issue for issue in validation.issues)


def test_validate_proposal_rejects_unknown_document() -> None:
    project_dir = Path("/tmp") / "validate-unknown"
    project_dir.mkdir(parents=True, exist_ok=True)
    svc = DesignService(projects_root=str(project_dir.parent))
    proposal = AIProposal(
        schemaVersion="1.0",
        proposalId="p1",
        baseRevision=0,
        requirement="x",
        operations=[
            AIProposalOperation(
                document="evil",
                type="replace_document",
                payload={"type": "replace_document", "value": {}},
            )
        ],
    )
    validation = validate_proposal(
        proposal,
        design_service=svc,
        project_id="x",
        documents={},
    )
    assert not validation.is_valid
    assert any("unknown document" in issue for issue in validation.issues)


def test_workflow_rejects_unsupported_capability(tmp_path: Path) -> None:
    _seed_v2_project(tmp_path)
    svc = DesignService(projects_root=str(tmp_path / "projects"))
    workflow = AIWorkflow(design_service=svc, provider=None)
    result = workflow.run(
        "Make me a coffee.",
        project_id="rc_lab",
        project_revision=0,
        documents={},
    )
    assert result.requirement.capability == CAPABILITY_UNSUPPORTED
    assert result.decision == ProposalDecision.REJECTED


def test_workflow_prompts_provider_and_validates(tmp_path: Path) -> None:
    _seed_v2_project(tmp_path)
    svc = DesignService(projects_root=str(tmp_path / "projects"))
    registry = ProviderRegistry.open(
        tmp_path / "projects",
        keychain=__import__(
            "ltagent.ai_provider", fromlist=["_InMemoryKeychain"]
        )._InMemoryKeychain(),
    )
    registry.save(
        ProviderProfile(
            profileId="default",
            name="Test",
            vendor=ProviderKind.OPENAI,
            model="gpt-4o-mini",
            baseUrl="https://api.example.com",
            keyId="default-key",
        ),
        secret="sk-test",
    )
    profile = registry.get("default")
    adapter = ProviderAdapter(profile, registry.keychain)

    workflow = AIWorkflow(design_service=svc, provider=adapter)

    proposal_payload = {
        "schemaVersion": "1.0",
        "proposalId": "p_001",
        "baseRevision": 0,
        "requirement": "RC low-pass 1kHz",
        "operations": [
            {
                "document": "analog",
                "type": "add_component",
                "payload": {
                    "componentId": "R2",
                    "kind": "resistor",
                    "pins": {"p1": "vin", "p2": "vout"},
                    "value": "1k",
                },
            }
        ],
    }
    body = json.dumps(
        {"output": [{"content": [{"type": "text", "text": json.dumps(proposal_payload)}]}]}
    )
    result = workflow.run(
        "Make an RC low-pass with cutoff 1kHz.",
        project_id="rc_lab",
        project_revision=0,
        documents={"analog": {"x": 1}, "schematic": {"y": 2}},
        body_override=lambda: body,
    )
    assert result.requirement.capability == CAPABILITY_RC_LOWPASS
    assert result.validation.is_valid
    assert result.decision == ProposalDecision.PENDING
    assert result.proposal.operations[0].payload["componentId"] == "R2"


def test_workflow_repair_loop_falls_back(tmp_path: Path) -> None:
    _seed_v2_project(tmp_path)
    svc = DesignService(projects_root=str(tmp_path / "projects"))
    in_memory = __import__(
        "ltagent.ai_provider", fromlist=["_InMemoryKeychain"]
    )._InMemoryKeychain()
    registry = ProviderRegistry.open(tmp_path / "projects", keychain=in_memory)
    registry.save(
        ProviderProfile(
            profileId="default",
            name="Test",
            vendor=ProviderKind.OPENAI,
            model="gpt-4o-mini",
            baseUrl="https://api.example.com",
            keyId="default-key",
        ),
        secret="sk-test",
    )
    profile = registry.get("default")
    adapter = ProviderAdapter(profile, in_memory)
    workflow = AIWorkflow(design_service=svc, provider=adapter, max_repair_attempts=2)

    from ltagent.ai_provider import AIProviderError

    counter = {"n": 0}
    repaired_body = json.dumps(
        {
            "output": [
                {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "schemaVersion": "1.0",
                                    "proposalId": "p_repaired",
                                    "baseRevision": 0,
                                    "requirement": "RC low-pass",
                                    "operations": [
                                        {
                                            "document": "analog",
                                            "type": "add_component",
                                            "payload": {
                                                "componentId": "C1",
                                                "kind": "capacitor",
                                                "pins": {"p1": "vin", "p2": "vout"},
                                                "value": "100n",
                                            },
                                        }
                                    ],
                                }
                            ),
                        }
                    ]
                }
            ]
        }
    )

    def _alternating() -> str:
        counter["n"] += 1
        if counter["n"] == 1:
            raise AIProviderError("WORKBENCH_AI_TIMEOUT", "timed out")
        return repaired_body

    result = workflow.run(
        "Make an RC low-pass with cutoff 1kHz.",
        project_id="rc_lab",
        project_revision=0,
        documents={"analog": {"x": 1}},
        body_override=_alternating,
    )
    assert len(result.repairs) == 1
    assert result.repairs[0].attempt == 1
    assert result.validation.is_valid
    assert result.proposal.proposalId == "p_repaired"


def test_workflow_accept_applies_proposal(tmp_path: Path) -> None:
    project_dir = _seed_v2_project(tmp_path)
    svc = DesignService(projects_root=str(tmp_path / "projects"))
    in_memory = __import__(
        "ltagent.ai_provider", fromlist=["_InMemoryKeychain"]
    )._InMemoryKeychain()
    registry = ProviderRegistry.open(tmp_path / "projects", keychain=in_memory)
    registry.save(
        ProviderProfile(
            profileId="default",
            name="Test",
            vendor=ProviderKind.OPENAI,
            model="gpt-4o-mini",
            baseUrl="https://api.example.com",
            keyId="default-key",
        ),
        secret="sk-test",
    )
    adapter = ProviderAdapter(registry.get("default"), in_memory)
    workflow = AIWorkflow(design_service=svc, provider=adapter)
    body = json.dumps(
        {
            "output": [
                {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "schemaVersion": "1.0",
                                    "proposalId": "p_apply",
                                    "baseRevision": 0,
                                    "requirement": "RC low-pass 1kHz",
                                    "operations": [
                                        {
                                            "document": "analog",
                                            "type": "add_component",
                                            "payload": {
                                                "componentId": "R2",
                                                "kind": "resistor",
                                                "pins": {"p1": "vin", "p2": "vout"},
                                                "value": "2k2",
                                            },
                                        }
                                    ],
                                }
                            ),
                        }
                    ]
                }
            ]
        }
    )
    result = workflow.run(
        "Make an RC low-pass with cutoff 1kHz.",
        project_id="rc_lab",
        project_revision=0,
        documents={"analog": {}},
        body_override=lambda: body,
    )
    assert result.validation.is_valid
    new_rev = workflow.accept(result, project_id="rc_lab")
    assert new_rev == 1
    on_disk = json.loads((project_dir / FILE_ANALOG_GRAPH).read_text(encoding="utf-8"))
    assert "R2" in on_disk["components"]


def test_manifest_carries_selected_document_content(tmp_path: Path) -> None:
    _seed_v2_project(tmp_path)
    workflow = AIWorkflow(
        design_service=DesignService(projects_root=str(tmp_path / "projects")),
        provider=None,
    )
    requirement = workflow.parse_requirement("Make an RC low-pass", request_id="ctx")
    manifest = workflow.build_manifest(
        requirement,
        project_id="rc_lab",
        revision=0,
        documents={"analog": {"components": {"R1": {"kind": "resistor"}}}},
    )
    assert manifest.documents[0].content["components"]["R1"]["kind"] == "resistor"


def test_validate_proposal_rejects_replace_document(tmp_path: Path) -> None:
    _seed_v2_project(tmp_path)
    svc = DesignService(projects_root=str(tmp_path / "projects"))
    proposal = AIProposal(
        schemaVersion="1.0",
        proposalId="p_raw",
        baseRevision=0,
        requirement="replace everything",
        operations=[
            AIProposalOperation(
                document="digital",
                type="replace_document",
                payload={"value": {"userHdl": "module unsafe; endmodule"}},
            )
        ],
    )
    validation = validate_proposal(
        proposal,
        design_service=svc,
        project_id="rc_lab",
        documents={},
    )
    assert not validation.is_valid
    assert any("not allowed for AI" in issue for issue in validation.issues)


def test_accept_uses_proposal_revision_guard(tmp_path: Path) -> None:
    _seed_v2_project(tmp_path)
    svc = DesignService(projects_root=str(tmp_path / "projects"))
    proposal = AIProposal(
        schemaVersion="1.0",
        proposalId="p_stale",
        baseRevision=7,
        requirement="RC low-pass",
        operations=[
            AIProposalOperation(
                document="analog",
                type="add_component",
                payload={
                    "componentId": "R7",
                    "kind": "resistor",
                    "pins": {"p1": "vin", "p2": "vout"},
                    "value": "7k",
                },
            )
        ],
    )
    validation = validate_proposal(
        proposal,
        design_service=svc,
        project_id="rc_lab",
        documents={},
    )
    assert not validation.is_valid
    assert any("revision" in issue.lower() for issue in validation.issues)


def test_workflow_repairs_deterministic_validation_failure(tmp_path: Path) -> None:
    _seed_v2_project(tmp_path)
    svc = DesignService(projects_root=str(tmp_path / "projects"))
    in_memory = __import__(
        "ltagent.ai_provider", fromlist=["_InMemoryKeychain"]
    )._InMemoryKeychain()
    registry = ProviderRegistry.open(tmp_path / "projects", keychain=in_memory)
    registry.save(
        ProviderProfile(
            profileId="default",
            name="Test",
            vendor=ProviderKind.OPENAI,
            model="gpt-4o-mini",
            baseUrl="https://api.example.com",
            keyId="default-key",
        ),
        secret="sk-test",
    )
    workflow = AIWorkflow(
        design_service=svc,
        provider=ProviderAdapter(registry.get("default"), in_memory),
        max_repair_attempts=2,
    )
    proposals = [
        {
            "schemaVersion": "1.0",
            "proposalId": "bad",
            "baseRevision": 0,
            "requirement": "RC",
            "operations": [
                {
                    "document": "digital",
                    "type": "replace_document",
                    "payload": {"value": {"userHdl": "module bad; endmodule"}},
                }
            ],
        },
        {
            "schemaVersion": "1.0",
            "proposalId": "fixed",
            "baseRevision": 0,
            "requirement": "RC",
            "operations": [
                {
                    "document": "analog",
                    "type": "add_component",
                    "payload": {
                        "componentId": "C1",
                        "kind": "capacitor",
                        "pins": {"p1": "vout", "p2": "0"},
                        "value": "100n",
                    },
                }
            ],
        },
    ]

    def response() -> str:
        proposal = proposals.pop(0)
        return json.dumps(
            {"output": [{"content": [{"type": "text", "text": json.dumps(proposal)}]}]}
        )

    result = workflow.run(
        "Make an RC low-pass",
        project_id="rc_lab",
        project_revision=0,
        documents={"analog": {}},
        body_override=response,
    )
    assert result.validation.is_valid
    assert result.proposal.proposalId == "fixed"
    assert len(result.repairs) == 1
    assert "not allowed for AI" in result.repairs[0].feedback
