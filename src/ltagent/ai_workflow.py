"""AI design workflow: RequirementSpec, AIProposal, validation, repair, apply.

The workflow is the orchestrator that ties the AI provider
(Phase 7), the design service (Phase 2), and the workbench
project (Phase 1) together. The flow is:

1. A natural-language prompt is parsed into a
   :class:`RequirementSpec` by the deterministic
   :class:`CapabilityClassifier` (the prompt parser is
   rule-based for v1, mirroring Phase 8's rule-based analog
   planner).
2. The :class:`AIWorkflow` builds an :class:`AIContextManifest`
   from the current project's documents, calls the provider,
   and validates the response against the typed
   :class:`AIProposal` schema.
3. The proposal is validated against the project's
   :class:`ltagent.design_service.DesignService` (revision
   check, document cross-validation, ground net, DRC). A
   failed validation triggers a repair attempt (up to three)
   that re-asks the provider with the failure evidence.
4. The accepted proposal is applied to the project as a
   revisioned :class:`ltagent.design_service.ChangeSet`. The
   workflow never auto-applies; the rendering layer is
   responsible for the Accept / Reject / Edit UI.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .ai_provider import (
    AIContextDocument,
    AIContextManifest,
    AIProposal,
    AIProviderError,
    ProviderAdapter,
)
from .design_service import ChangeSet, DesignService, WorkbenchV2Error

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUIREMENT_SPEC_SCHEMA_VERSION: Final[str] = "1.0"
MAX_REPAIR_ATTEMPTS: Final[int] = 3
AI_OPERATION_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "add_component",
        "remove_component",
        "set_component_value",
        "rename_component",
        "connect_pin",
        "disconnect_pin",
        "rename_net",
        "add_directive",
        "add_measurement",
        "place_node",
        "move_node",
        "rotate_node",
        "delete_node",
        "set_node_properties",
        "set_wire_route",
        "remove_wire",
        "set_net_label",
        "set_grid_size",
        "set_digital_design",
    }
)

# Capability classification labels. The classifier returns one
# of these strings; the workflow keys the proposal context
# off the label.
CAPABILITY_RC_LOWPASS: Final[str] = "rc_lowpass"
CAPABILITY_RC_HIGHPASS: Final[str] = "rc_highpass"
CAPABILITY_OPAMP_INVERTING: Final[str] = "opamp_inverting"
CAPABILITY_OPAMP_NONINVERTING: Final[str] = "opamp_noninverting"
CAPABILITY_COUNTER_8BIT: Final[str] = "counter_8bit"
CAPABILITY_FSM_BLINK: Final[str] = "fsm_blink"
CAPABILITY_PWM: Final[str] = "pwm"
CAPABILITY_UNSUPPORTED: Final[str] = "unsupported"

SUPPORTED_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {
        CAPABILITY_RC_LOWPASS,
        CAPABILITY_RC_HIGHPASS,
        CAPABILITY_OPAMP_INVERTING,
        CAPABILITY_OPAMP_NONINVERTING,
        CAPABILITY_COUNTER_8BIT,
        CAPABILITY_FSM_BLINK,
        CAPABILITY_PWM,
    }
)

# Stable error codes for the AI workflow layer.
ERR_AI_REQUIREMENT_INVALID: Final[str] = "WORKBENCH_AI_REQUIREMENT_INVALID"
ERR_AI_PROPOSAL_REJECTED: Final[str] = "WORKBENCH_AI_PROPOSAL_REJECTED"
ERR_AI_REPAIR_EXHAUSTED: Final[str] = "WORKBENCH_AI_REPAIR_EXHAUSTED"
ERR_AI_REVISION_CONFLICT: Final[str] = "WORKBENCH_AI_REVISION_CONFLICT"


class ProposalDecision(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EDITED = "edited"


# ---------------------------------------------------------------------------
# RequirementSpec
# ---------------------------------------------------------------------------


class RequirementSpec(BaseModel):
    """The structured output of the prompt parser.

    The parser is deterministic for v1 (rule-based). Phase 8
    keeps the surface narrow: a domain, a capability label, the
    raw prompt, a list of constraints, and an optional safety
    class. The AI workflow rejects :class:`RequirementSpec`
    objects whose ``capability`` is ``unsupported``.
    """

    model_config = ConfigDict(extra="forbid")

    schemaVersion: str
    requestId: str
    domain: str
    intent: str
    capability: str
    text: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    safetyClass: str | None = None
    language: str = "en"

    @field_validator("schemaVersion")
    @classmethod
    def _version(cls, v: str) -> str:
        if v != REQUIREMENT_SPEC_SCHEMA_VERSION:
            raise ValueError(f"RequirementSpec schemaVersion {v!r} is not supported")
        return v

    @field_validator("capability")
    @classmethod
    def _capability_known(cls, v: str) -> str:
        if v not in SUPPORTED_CAPABILITIES and v != CAPABILITY_UNSUPPORTED:
            raise ValueError(f"capability {v!r} is not in the v1 allowlist")
        return v


# ---------------------------------------------------------------------------
# Capability classifier
# ---------------------------------------------------------------------------


_RC_LOWPASS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brc\s*low\s*-?\s*pass\b", re.IGNORECASE),
    re.compile(r"\bfilter\s*low\s*-?\s*pass\b", re.IGNORECASE),
    re.compile(r"\bfilter\s+rendah\b", re.IGNORECASE),
    re.compile(r"\blowpass\b", re.IGNORECASE),
    re.compile(r"\bcutoff\s+(\d+(\.\d+)?)\s*k?hz\b", re.IGNORECASE),
)
_RC_HIGHPASS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brc\s*high\s*-?\s*pass\b", re.IGNORECASE),
    re.compile(r"\bfilter\s*tinggi\b", re.IGNORECASE),
    re.compile(r"\bhighpass\b", re.IGNORECASE),
)
_OPAMP_INVERTING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\binverting\s+op\s*-?\s*amp\b", re.IGNORECASE),
    re.compile(r"\binverting\s+amplifier\b", re.IGNORECASE),
    re.compile(r"\bop\s*-?\s*amp\s+inverting\b", re.IGNORECASE),
    re.compile(r"\bopamp\s+inverting\b", re.IGNORECASE),
)
_OPAMP_NONINVERTING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bnon\s*-?\s*inverting\s+op\s*-?\s*amp\b", re.IGNORECASE),
    re.compile(r"\bnon\s*-?\s*inverting\s+amplifier\b", re.IGNORECASE),
)
_COUNTER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(\d+)\s*-?\s*bit\s+counter\b", re.IGNORECASE),
    re.compile(r"\bcounter\s+(\d+)\s*-?\s*bit\b", re.IGNORECASE),
    re.compile(r"\bcacah\b", re.IGNORECASE),
)
_FSM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bblink(ing)?\s+led\b", re.IGNORECASE),
    re.compile(r"\bfsm\b", re.IGNORECASE),
    re.compile(r"\bkelap\s*-?\s*kelip\b", re.IGNORECASE),
)
_PWM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bpwm\b", re.IGNORECASE),
    re.compile(r"\bpulse\s*-?\s*width\s+modulation\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class ClassificationResult:
    capability: str
    constraints: dict[str, Any]
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "constraints": dict(self.constraints),
            "rationale": self.rationale,
        }


class CapabilityClassifier:
    """Deterministic v1 classifier for AI workflow prompts."""

    def classify(self, prompt: str) -> ClassificationResult:
        text = prompt.strip()
        constraints: dict[str, Any] = {}

        if any(pat.search(text) for pat in _RC_HIGHPASS_PATTERNS):
            return ClassificationResult(
                capability=CAPABILITY_RC_HIGHPASS,
                constraints=constraints,
                rationale="matched RC high-pass pattern",
            )
        if any(pat.search(text) for pat in _RC_LOWPASS_PATTERNS):
            cutoff = self._extract_cutoff_hz(text)
            if cutoff is not None:
                constraints["cutoffHz"] = cutoff
            return ClassificationResult(
                capability=CAPABILITY_RC_LOWPASS,
                constraints=constraints,
                rationale="matched RC low-pass pattern",
            )
        if any(pat.search(text) for pat in _OPAMP_NONINVERTING_PATTERNS):
            return ClassificationResult(
                capability=CAPABILITY_OPAMP_NONINVERTING,
                constraints=constraints,
                rationale="matched non-inverting op-amp pattern",
            )
        if any(pat.search(text) for pat in _OPAMP_INVERTING_PATTERNS):
            gain = self._extract_gain(text)
            if gain is not None:
                constraints["gainMagnitude"] = gain
            return ClassificationResult(
                capability=CAPABILITY_OPAMP_INVERTING,
                constraints=constraints,
                rationale="matched inverting op-amp pattern",
            )
        if any(pat.search(text) for pat in _FSM_PATTERNS):
            return ClassificationResult(
                capability=CAPABILITY_FSM_BLINK,
                constraints=constraints,
                rationale="matched FSM / blink pattern",
            )
        if any(pat.search(text) for pat in _PWM_PATTERNS):
            return ClassificationResult(
                capability=CAPABILITY_PWM,
                constraints=constraints,
                rationale="matched PWM pattern",
            )
        counter_match = self._match_counter(text)
        if counter_match is not None:
            constraints["width"] = counter_match
            return ClassificationResult(
                capability=CAPABILITY_COUNTER_8BIT,
                constraints=constraints,
                rationale=f"matched counter pattern (width={counter_match})",
            )
        return ClassificationResult(
            capability=CAPABILITY_UNSUPPORTED,
            constraints=constraints,
            rationale="no v1 capability matched the prompt",
        )

    @staticmethod
    def _extract_cutoff_hz(text: str) -> float | None:
        match = re.search(r"cutoff\s+(\d+(?:\.\d+)?)\s*k?hz", text, re.IGNORECASE)
        if not match:
            return None
        value = float(match.group(1))
        return value * 1000 if "khz" in text[match.start() : match.end()].lower() else value

    @staticmethod
    def _extract_gain(text: str) -> int | None:
        match = re.search(r"gain\s*(-?\d+)", text, re.IGNORECASE)
        return int(match.group(1)) if match else None

    @staticmethod
    def _match_counter(text: str) -> int | None:
        match = re.search(r"(\d+)\s*-?\s*bit\s+counter", text, re.IGNORECASE)
        if match:
            return int(match.group(1))
        match = re.search(r"counter\s+(\d+)\s*-?\s*bit", text, re.IGNORECASE)
        return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProposalValidation:
    is_valid: bool
    issues: tuple[str, ...]
    warnings: tuple[str, ...]
    impact: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "isValid": self.is_valid,
            "issues": list(self.issues),
            "warnings": list(self.warnings),
            "impact": dict(self.impact),
        }


def validate_proposal(
    proposal: AIProposal,
    *,
    design_service: DesignService,
    project_id: str,
    documents: Mapping[str, dict[str, Any]],
) -> ProposalValidation:
    """Validate a proposal against the project documents.

    The validation is read-only: it never mutates the project.
    Issues are returned as a tuple so the caller can render
    them in the AI workflow UI. Warnings do not block the
    proposal; issues do.
    """
    issues: list[str] = []
    warnings: list[str] = []
    impact: dict[str, Any] = {"documents": set(), "components": set(), "nets": set()}

    if not proposal.operations:
        issues.append("proposal contains no operations")
    if proposal.baseRevision < 0:
        issues.append("proposal baseRevision is negative")

    for index, op in enumerate(proposal.operations):
        if op.document not in {"requirements", "analog", "schematic", "digital", "system"}:
            issues.append(f"operation {index}: unknown document {op.document!r}")
            continue
        payload = op.payload
        if not isinstance(payload, dict):
            issues.append(f"operation {index}: payload is not an object")
            continue
        op_type = op.type
        if not isinstance(op_type, str) or not op_type:
            issues.append(f"operation {index}: missing operation type")
            continue
        if op_type not in AI_OPERATION_ALLOWLIST:
            issues.append(f"operation {index}: {op_type!r} is not allowed for AI")
            continue
        impact["documents"].add(op.document)
        if op_type == "add_component":
            cid = payload.get("componentId")
            if not isinstance(cid, str) or not cid:
                issues.append(f"operation {index}: add_component missing componentId")
            else:
                impact["components"].add(cid)
        elif op_type == "connect_pin":
            net = payload.get("net")
            if isinstance(net, str):
                impact["nets"].add(net)

    if not issues:
        change_set = {
            "schemaVersion": "2.0",
            "baseRevision": proposal.baseRevision,
            "actor": "ai-preview",
            "clientRequestId": proposal.proposalId,
            "operations": [
                {"document": op.document, "type": op.type, **op.payload}
                for op in proposal.operations
            ],
            "validationPlan": proposal.validationPlan,
        }
        try:
            design_service.preview_change_set(project_id, change_set)
        except WorkbenchV2Error as exc:
            issues.append(f"{exc.code}: {exc.message}")

    return ProposalValidation(
        is_valid=not issues,
        issues=tuple(issues),
        warnings=tuple(warnings),
        impact={
            "documents": sorted(impact["documents"]),
            "components": sorted(impact["components"]),
            "nets": sorted(impact["nets"]),
        },
    )


# ---------------------------------------------------------------------------
# Repair loop
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepairAttempt:
    attempt: int
    feedback: str
    proposal: AIProposal | None


@dataclass(frozen=True)
class WorkflowResult:
    requirement: RequirementSpec
    proposal: AIProposal
    validation: ProposalValidation
    repairs: tuple[RepairAttempt, ...]
    decision: ProposalDecision
    revisionAfter: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement": self.requirement.model_dump(mode="json"),
            "proposal": self.proposal.model_dump(mode="json"),
            "validation": self.validation.to_dict(),
            "repairs": [
                {
                    "attempt": repair.attempt,
                    "feedback": repair.feedback,
                    "hasProposal": repair.proposal is not None,
                }
                for repair in self.repairs
            ],
            "decision": self.decision.value,
            "revisionAfter": self.revisionAfter,
        }


class AIWorkflow:
    """The orchestrator that ties parser, provider, validator, and applier together.

    The workflow is intentionally synchronous. The provider
    call is the only async-shaped step; everything else is pure
    Python and bounded.
    """

    def __init__(
        self,
        *,
        design_service: DesignService,
        provider: ProviderAdapter | None = None,
        classifier: CapabilityClassifier | None = None,
        max_repair_attempts: int = MAX_REPAIR_ATTEMPTS,
    ) -> None:
        self.design_service = design_service
        self.provider = provider
        self.classifier = classifier or CapabilityClassifier()
        self.max_repair_attempts = max_repair_attempts

    def parse_requirement(self, prompt: str, *, request_id: str) -> RequirementSpec:
        classification = self.classifier.classify(prompt)
        try:
            return RequirementSpec(
                schemaVersion=REQUIREMENT_SPEC_SCHEMA_VERSION,
                requestId=request_id,
                domain="analog"
                if classification.capability.startswith("rc_")
                or classification.capability.startswith("opamp_")
                else "digital",
                intent=classification.capability,
                capability=classification.capability,
                text=prompt,
                constraints=classification.constraints,
            )
        except ValidationError as exc:
            raise AIProviderError(
                ERR_AI_REQUIREMENT_INVALID,
                f"requirement is not valid: {exc}",
                data={"errors": exc.errors()},
            ) from exc

    def build_manifest(
        self,
        requirement: RequirementSpec,
        *,
        project_id: str,
        revision: int,
        documents: Mapping[str, dict[str, Any]],
    ) -> AIContextManifest:
        items: list[AIContextDocument] = []
        total = 0
        for kind, payload in documents.items():
            text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            size = len(text.encode("utf-8"))
            total += size
            items.append(
                AIContextDocument(
                    kind=kind,
                    title=f"{project_id}/{kind}",
                    sha256=_hash_bytes(text.encode("utf-8")),
                    size=size,
                    content=dict(payload),
                )
            )
        return AIContextManifest(
            projectId=project_id,
            revision=revision,
            prompt=self._compose_prompt(requirement),
            documents=items,
            estimatedBytes=total,
            provider="openai",
            model=self.provider.profile.model if self.provider is not None else "unknown",
        )

    def _compose_prompt(self, requirement: RequirementSpec) -> str:
        return (
            f"Capability: {requirement.capability}\n"
            f"Constraints: {json.dumps(requirement.constraints, sort_keys=True)}\n"
            f"Text: {requirement.text}\n"
            "Produce an AIProposal JSON document with operations that target "
            "the analog, schematic, requirements, digital, or system document."
        )

    def request_proposal(
        self,
        requirement: RequirementSpec,
        manifest: AIContextManifest,
        *,
        body_override: Any = None,
    ) -> AIProposal:
        if self.provider is None:
            raise AIProviderError(
                "WORKBENCH_AI_PROVIDER_NOT_CONFIGURED",
                "no provider configured",
            )
        return self.provider.create_response(manifest, body_override=body_override)

    def repair(
        self,
        requirement: RequirementSpec,
        manifest: AIContextManifest,
        *,
        previous_feedback: str,
        body_override: Any = None,
    ) -> AIProposal:
        if self.provider is None:
            raise AIProviderError(
                "WORKBENCH_AI_PROVIDER_NOT_CONFIGURED",
                "no provider configured",
            )
        amended = manifest.model_copy(
            update={"prompt": manifest.prompt + "\n\n" + previous_feedback}
        )
        return self.provider.create_response(amended, body_override=body_override)

    def run(
        self,
        prompt: str,
        *,
        project_id: str,
        project_revision: int,
        documents: Mapping[str, dict[str, Any]],
        request_id: str = "req_local",
        body_override: Any = None,
    ) -> WorkflowResult:
        requirement = self.parse_requirement(prompt, request_id=request_id)
        if requirement.capability == CAPABILITY_UNSUPPORTED:
            return WorkflowResult(
                requirement=requirement,
                proposal=AIProposal(
                    schemaVersion="1.0",
                    proposalId=f"prop_{request_id}_unsupported",
                    baseRevision=project_revision,
                    requirement=prompt,
                    operations=[],
                ),
                validation=ProposalValidation(
                    is_valid=False,
                    issues=("capability is unsupported",),
                    warnings=(),
                ),
                repairs=(),
                decision=ProposalDecision.REJECTED,
            )
        manifest = self.build_manifest(
            requirement, project_id=project_id, revision=project_revision, documents=documents
        )
        repairs: list[RepairAttempt] = []
        try:
            proposal = self.request_proposal(requirement, manifest, body_override=body_override)
        except AIProviderError as exc:
            for attempt in range(1, self.max_repair_attempts + 1):
                feedback = f"Provider error ({exc.code}): {exc.message}"
                try:
                    proposal = self.repair(
                        requirement,
                        manifest,
                        previous_feedback=feedback,
                        body_override=body_override,
                    )
                except AIProviderError as repair_exc:
                    repairs.append(
                        RepairAttempt(attempt=attempt, feedback=str(repair_exc), proposal=None)
                    )
                    continue
                repairs.append(RepairAttempt(attempt=attempt, feedback=feedback, proposal=proposal))
                break
            else:
                raise AIProviderError(
                    ERR_AI_REPAIR_EXHAUSTED,
                    f"repair exhausted after {self.max_repair_attempts} attempts",
                ) from exc
        validation = validate_proposal(
            proposal,
            design_service=self.design_service,
            project_id=project_id,
            documents=documents,
        )
        while not validation.is_valid and len(repairs) < self.max_repair_attempts:
            feedback = "Deterministic validation failed:\n- " + "\n- ".join(
                validation.issues
            )
            attempt = len(repairs) + 1
            try:
                proposal = self.repair(
                    requirement,
                    manifest,
                    previous_feedback=feedback,
                    body_override=body_override,
                )
            except AIProviderError as exc:
                repairs.append(RepairAttempt(attempt=attempt, feedback=str(exc), proposal=None))
                continue
            repairs.append(RepairAttempt(attempt=attempt, feedback=feedback, proposal=proposal))
            validation = validate_proposal(
                proposal,
                design_service=self.design_service,
                project_id=project_id,
                documents=documents,
            )
        return WorkflowResult(
            requirement=requirement,
            proposal=proposal,
            validation=validation,
            repairs=tuple(repairs),
            decision=(
                ProposalDecision.PENDING if validation.is_valid else ProposalDecision.REJECTED
            ),
        )

    def accept(
        self,
        workflow_result: WorkflowResult,
        *,
        project_id: str,
        actor: str = "ai",
    ) -> int:
        """Apply the proposal to the project as a revisioned ChangeSet.

        The caller (the rendering layer) is responsible for
        asking the user before invoking :meth:`accept`. The
        workflow never auto-applies.
        """
        if not workflow_result.validation.is_valid:
            raise AIProviderError(
                ERR_AI_PROPOSAL_REJECTED,
                "proposal is not valid; cannot apply",
                data={"issues": list(workflow_result.validation.issues)},
            )
        change_set = ChangeSet.model_validate(
            {
                "schemaVersion": "2.0",
                "baseRevision": workflow_result.proposal.baseRevision,
                "actor": actor,
                "clientRequestId": workflow_result.proposal.proposalId,
                "operations": [
                    {"document": op.document, "type": op.type, **op.payload}
                    for op in workflow_result.proposal.operations
                ],
            }
        )
        try:
            result = self.design_service.apply_change_set(
                project_id, change_set.model_dump(mode="json")
            )
        except WorkbenchV2Error as exc:
            raise AIProviderError(
                ERR_AI_REVISION_CONFLICT,
                f"apply failed: {exc.message}",
                data={"code": exc.code, "details": exc.data},
            ) from exc
        return result.revision


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_bytes(payload: bytes) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


__all__ = [
    "CAPABILITY_COUNTER_8BIT",
    "CAPABILITY_FSM_BLINK",
    "CAPABILITY_OPAMP_INVERTING",
    "CAPABILITY_OPAMP_NONINVERTING",
    "CAPABILITY_PWM",
    "CAPABILITY_RC_HIGHPASS",
    "CAPABILITY_RC_LOWPASS",
    "CAPABILITY_UNSUPPORTED",
    "ERR_AI_PROPOSAL_REJECTED",
    "ERR_AI_REPAIR_EXHAUSTED",
    "ERR_AI_REQUIREMENT_INVALID",
    "ERR_AI_REVISION_CONFLICT",
    "MAX_REPAIR_ATTEMPTS",
    "REQUIREMENT_SPEC_SCHEMA_VERSION",
    "SUPPORTED_CAPABILITIES",
    "AIWorkflow",
    "CapabilityClassifier",
    "ClassificationResult",
    "ProposalDecision",
    "ProposalValidation",
    "RepairAttempt",
    "RequirementSpec",
    "WorkflowResult",
    "validate_proposal",
]
