# Phase 8 Exit Gate — AI Design Workflow

Date: 2026-06-20
Branch: `main`
Commit: `883a2cf`

## Scope

Add the AI design workflow on top of Phase 7 (ProviderProfile/Adapter/Registry +
keyring + AIContextManifest + secret/injection detection). Phase 8 closes the
loop from user prompt → capability classification → provider call → validated
proposal → applied ChangeSet, all on the existing v2 design service.

## Files

- `src/ltagent/ai_workflow.py` — `RequirementSpec`, `CapabilityClassifier`,
  `AIWorkflow`, `ProposalValidation`, `validate_proposal`, `RepairAttempt`,
  `WorkflowResult`, `ProposalDecision`, capability constants.
- `tests/test_ai_workflow.py` — 13 tests.

## Acceptance

- `RequirementSpec` validates domain, capability (enum-backed), intent, text,
  request id; rejects unknown capability with `ValidationError`.
- `CapabilityClassifier` recognises English and Indonesian prompts for:
  - `rc_lowpass` ("RC low-pass filter with cutoff 1kHz",
    "Buat filter RC lowpass dengan cutoff 1kHz")
  - `rc_highpass` ("RC high-pass filter",
    "Filter tinggi dengan cutoff 10kHz")
  - `opamp_inverting` ("inverting op-amp with gain -10")
  - `opamp_noninverting`
  - `counter_8bit` ("8-bit counter")
  - `fsm_blink` ("blinking LED fsm")
  - `pwm`
  - unsupported ("make me a coffee") → `capability="unsupported"`
- `validate_proposal` rules:
  - empty `operations` → `proposal contains no operations`
  - unknown document → `operation N: unknown document 'X'`
  - non-dict payload → `operation N: payload is not an object`
  - missing operation `type` → `operation N: missing operation type`
  - `add_component` without `componentId` → warning surfaced
- `AIWorkflow.run`:
  - unsupported capability returns `WorkflowResult(decision=REJECTED)`
    without calling the provider
  - supported capability builds the `AIContextManifest` and calls the
    provider; on `AIProviderError` falls back through a bounded repair loop
    (`max_repair_attempts`, default 3); exhaustion raises
    `AIProviderError(ERR_AI_REPAIR_EXHAUSTED)`
  - validates the proposal and returns `decision=PENDING|REJECTED`
- `AIWorkflow.accept`:
  - rejects unvalidated proposals with `ERR_AI_PROPOSAL_REJECTED`
  - applies the proposal through `DesignService.apply_change_set` as a
    revisioned `ChangeSet`; revision conflicts become
    `AIProviderError(ERR_AI_REVISION_CONFLICT)`
  - the workflow never auto-applies — the rendering layer is responsible
    for asking the user

## Numbers

- `pytest -q`: **1462 passed**, 15 skipped, 3 failed
  (3 pre-existing MCP-SDK env gap, unchanged from Phase 0 baseline).
- `ruff check src/ltagent/ai_workflow.py tests/test_ai_workflow.py`: clean.
- `mypy src/ltagent/ai_workflow.py`: clean.

## Invariants

- API key handling: keyring-backed; never written to disk; never logged;
  never sent to the client.
- Prompt-injection detection still gates the `create_response` call.
- Operations reuse the typed `DesignService` op set; the workflow never
  invents a new operation type.
- `AIWorkflow` is the only path that calls the provider on the v2
  contract; `DesignService` is the only path that writes project
  documents.
- Repair loop is bounded and structured; exhaustion surfaces a stable
  code so the UI can show actionable feedback.

## Next

Phase 9 — Codex MCP: curated workbench v2 tools
(`ltagent codex install/doctor/uninstall`) and an end-to-end Codex↔desktop
round trip on the v2 contract.
