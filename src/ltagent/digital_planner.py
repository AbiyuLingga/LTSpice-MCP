"""Phase 12: Rule-based planner for natural-language Design IR generation.

Per the Phase 12 plan (docs/digital/plan-tiny8-agent.md) section 7,
this module is a *deterministic* parser for a small, well-defined
set of v1 digital prompts in English and Indonesian. It does not
call an LLM, does not parse arbitrary English, and does not attempt
to handle ambiguous phrasing. Unrecognised prompts return a
structured ``PlannerRefusal`` so the CLI layer can present the
supported prompt formats.

Design rules (mirror the analog planner):

* Pure functions only. No filesystem I/O. No subprocess. The
  planner's sole responsibility is
  ``str -> DesignIR | PlannerRefusal | ClarificationRequest | RoadmapSuggestion``.
* The planner never invents a project name from nothing: it
  builds the name from the design kind and a counter; all
  lower-case, all safe-slug.
* The planner never emits Verilog. It emits ``DesignIR`` only;
  the generator (Phase C) is the only thing that writes HDL.
* Refusal objects are frozen dataclasses with stable ``code``
  fields. RoadmapSuggestion is the *positive* answer for prompts
  that are valid directions but not v1 scope.

Supported v1 prompts (per plan section 7.2):

========================================= ===========================================
Prompt                                   Resulting ``DesignIR.kind``
========================================= ===========================================
``buat mini processor 8-bit sederhana``  ``tiny8_cpu``
``create tiny 8-bit CPU add 20 22 halt``  ``tiny8_cpu``
``buat CPU 8-bit accumulator``           ``tiny8_cpu``
``buat tiny computer dengan RAM ROM``    ``tiny8_soc`` (reserved; v1 emits roadmap)
``ltagent digital create <ir-file>``     ``tiny8_cpu`` (from hand-written IR)
========================================= ===========================================

The default program is "add 20 and 22, store at RAM[0x10],
store the result at RAM[0x11], halt". This is the same demo
the testbench in Phase C will use.

Out of scope for Phase 12 (deliberately):

* Free-form English / Indonesian via an LLM.
* Multi-stage conversations or prompt refinement.
* Wider Tiny8 ISAs, RV32I, full mini PC, USB, HDMI, Linux.
  These produce a structured ``RoadmapSuggestion``.
* Free-form RTL description. The IR is structural; HDL comes
  from templates only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from .digital_ir import (
    ClockSpec,
    CpuSpec,
    DesignIR,
    ExpectedState,
    IoSpec,
    MemorySpec,
    Metadata,
    ProgramSpec,
    ResetSpec,
    VerificationSpec,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REFUSAL_UNSUPPORTED_PROMPT: Final[str] = "UNSUPPORTED_PROMPT"
_REFUSAL_MISSING_PARAM: Final[str] = "MISSING_PARAM"
_REFUSAL_INVALID_VALUE: Final[str] = "INVALID_VALUE"
_REFUSAL_AMBIGUOUS_PROMPT: Final[str] = "AMBIGUOUS_PROMPT"
_REFUSAL_PROMPT_INJECTION: Final[str] = "PROMPT_INJECTION"

#: Categories used by the planner. These are stable strings the CLI
#: and MCP surface can switch on.
_CATEGORY_TINY8_CPU: Final[str] = "tiny8_cpu"
_CATEGORY_TINY8_SOC: Final[str] = "tiny8_soc"
_CATEGORY_RISCV: Final[str] = "riscv_request"
_CATEGORY_MINI_PC: Final[str] = "mini_pc_full"
_CATEGORY_ANALOG: Final[str] = "analog_circuit"
_CATEGORY_AMBIGUOUS: Final[str] = "ambiguous"
_CATEGORY_UNSAFE: Final[str] = "unsafe"

#: Tokens that flag prompt injection / unsafe input. These are
#: matched at the normalised prompt level (lower-case, single
#: spaces) so the planner never has to do clever parsing on
#: malformed input. The patterns are intentionally broad enough
#: to catch obvious attempts and intentionally narrow enough to
#: avoid false positives on legitimate technical prose.
_INJECTION_TOKENS: Final[tuple[str, ...]] = (
    "rm -rf",
    "del /f",
    "format c:",
    "/etc/passwd",
    "$(rm",
    "`rm",
    "; rm",
    "&& rm",
    "&&del",
    "wget ",
    "curl ",
    "powershell -e",
    "import os",
    "exec(",
    "eval(",
    "subprocess",
    "os.system",
    "shell=True",
    "write_file",
    "read_file",
    "run_shell",
    "execute_python",
    "drop table",
    "select * from",
    "ignore previous",
    "ignore all",
    "forget everything",
    "system prompt",
    "you are now",
    "act as",
    "do anything now",
)

#: Keywords that explicitly mark a prompt as "not v1" and
#: deserve a roadmap answer instead of a refusal.
_ROADMAP_KEYWORDS: Final[tuple[str, ...]] = (
    "risc-v",
    "riscv",
    "rv32",
    "rv64",
    "linux",
    "intel nuc",
    "raspberry pi",
    "motherboard",
    "full mini pc",
    "mini pc",
    "pc lengkap",
    "gpu",
    "usb",
    "hdmi",
    "ethernet",
    "wifi",
    "bluetooth",
    "operating system",
    "sistem operasi",
    "pipeline",
    "cache l",
    "mmu",
    "interrupt controller",
    "pcie",
    "sata",
    "nand",
    "display controller",
)

#: Patterns that detect "Tiny8" intent (English + Indonesian).
_TINY8_PATTERNS: Final[tuple[str, ...]] = (
    r"\b(tiny\s*8|tiny8)\b",
    r"\bprocessor\s+8[\s-]?bit\b",
    r"\bcpu\s+8[\s-]?bit\b",
    r"\bmini\s+processor\b",
    r"\bmini\s+cpu\b",
    r"\b8[\s-]?bit\s+(cpu|processor|mikroprosesor)\b",
    r"\b(cpu|processor|mikroprosesor)\s+8[\s-]?bit\b",
    r"\bmikroprosesor\s+8[\s-]?bit\b",
)

#: Patterns that detect SoC intent (CPU + memory-mapped IO).
_SOC_PATTERNS: Final[tuple[str, ...]] = (
    r"\btiny\s+computer\b",
    r"\btiny\s+soc\b",
    r"\bmemory[\s-]?mapped\s+io\b",
    r"\bmmio\b",
)

#: Patterns that detect explicit arithmetic intent (helps choose
#: the demo program). The assembler pattern lives in Phase C; the
#: planner just records the intent for the verification contract.
_ADD_INTENT: Final[tuple[str, ...]] = (
    r"\b(add|tambah|jumlah|sum)\b",
    r"\b(20|22)\b",
)


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannerRefusal:
    """Structured refusal returned by :func:`plan_digital_prompt`.

    Mirrors the JSON shape in plan section 7.1. Frozen so callers
    cannot mutate the structured output after the planner returns it.
    """

    code: str
    message: str
    supported_kinds: tuple[str, ...]
    next_step: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "supportedKinds": list(self.supported_kinds),
            "nextStep": self.next_step,
            "data": dict(self.data),
        }


@dataclass(frozen=True)
class ClarificationRequest:
    """A prompt that is *recognised* but missing a required field.

    The caller can either re-prompt with the missing field, or
    accept the default suggested by the planner. The MCP surface
    surfaces this as ``needsClarification: true``.
    """

    code: str
    message: str
    question: str
    options: tuple[str, ...]
    default: str
    supported_kinds: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "question": self.question,
            "options": list(self.options),
            "default": self.default,
            "supportedKinds": list(self.supported_kinds),
        }


@dataclass(frozen=True)
class RoadmapSuggestion:
    """A prompt that names a *valid direction* but is not v1.

    The MCP surface surfaces this with ``roadmap: true`` so
    callers know the request was understood and rejected on
    scope, not on understanding.
    """

    code: str
    message: str
    category: str
    why_not_v1: str
    proposed_phases: tuple[str, ...]
    next_step: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "category": self.category,
            "whyNotV1": self.why_not_v1,
            "proposedPhases": list(self.proposed_phases),
            "nextStep": self.next_step,
        }


# Discriminated union for the planner return type.
PlannerResult = DesignIR | PlannerRefusal | ClarificationRequest | RoadmapSuggestion


# ---------------------------------------------------------------------------
# Default design shape
# ---------------------------------------------------------------------------

#: Default Tiny8 CPU template. Everything except the program and
#: verification contract is identical across v1 instances.
_DEFAULT_CLOCK_HZ: Final[int] = 1_000_000  # 1 MHz — a friendly demo
_DEFAULT_PROGRAM_SOURCE: Final[str] = "demo.asm"
_DEFAULT_PROGRAM_ENTRY: Final[int] = 0
_DEFAULT_HALT_CYCLES: Final[int] = 200
_DEFAULT_DEMO_PROGRAM: Final[tuple[tuple[str, int], ...]] = (
    ("LDI", 20),
    ("STA", 0x10),
    ("LDI", 22),
    ("ADD", 0x10),
    ("STA", 0x11),
    ("HALT", 0),
)
_DEFAULT_DEMO_EXPECTED_ACC: Final[int] = 42  # 20 + 22
_DEFAULT_DEMO_EXPECTED_MEM: Final[dict[int, int]] = {0x10: 20, 0x11: 42}

_SUPPORTED_KINDS: Final[tuple[str, ...]] = ("tiny8_cpu", "tiny8_soc")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def plan_digital_prompt(prompt: str) -> PlannerResult:
    """Parse a natural-language prompt and return a DesignIR, refusal,
    clarification request, or roadmap suggestion.

    The function is total: every input string yields exactly one
    result. It never raises on user-visible errors. Internal
    programmer errors (e.g. an IR validator failure that contradicts
    the planner) still propagate.
    """
    if not isinstance(prompt, str):
        raise TypeError(f"prompt must be str, got {type(prompt).__name__}")
    text = _normalize(prompt)
    if not text:
        return PlannerRefusal(
            code=_REFUSAL_UNSUPPORTED_PROMPT,
            message="Prompt is empty",
            supported_kinds=_SUPPORTED_KINDS,
            next_step=(
                "Provide a prompt in English or Indonesian, e.g. "
                "'buat mini processor 8-bit sederhana' or 'create a "
                "tiny 8-bit CPU that adds two numbers and halts'."
            ),
            data={"rawPrompt": prompt},
        )

    # 1. Safety check first. If the prompt asks for filesystem or
    #    shell access, return a hard refusal before any other
    #    classification runs.
    if _is_unsafe(text):
        return PlannerRefusal(
            code=_REFUSAL_PROMPT_INJECTION,
            message=(
                "Prompt contains tokens that suggest filesystem, shell, "
                "or prompt-injection content. The planner is "
                "rule-based and cannot execute commands."
            ),
            supported_kinds=_SUPPORTED_KINDS,
            next_step=(
                "Rephrase the prompt as a digital design request, e.g. "
                "'buat mini processor 8-bit sederhana yang menjumlahkan "
                "20 dan 22'."
            ),
            data={"rawPrompt": prompt},
        )

    # 2. Roadmap keywords short-circuit. If the prompt mentions
    #    RV32I / RISC-V / full mini PC / GPU / USB / HDMI / etc.,
    #    the answer is a structured roadmap, not a refusal.
    roadmap_hit = _detect_roadmap_keyword(text)
    if roadmap_hit is not None:
        return _build_roadmap(roadmap_hit, prompt, text)

    # 3. Tiny8 / Tiny8 SoC classification.
    if _matches_any(text, _TINY8_PATTERNS):
        return _plan_tiny8_cpu(prompt, text)
    if _matches_any(text, _SOC_PATTERNS):
        return _plan_tiny8_soc(prompt, text)

    # 4. Otherwise: ambiguous. Tell the caller the supported shapes.
    return _ambiguous_refusal(prompt, text)


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def _normalize(prompt: str) -> str:
    s = prompt.strip()
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"\s+", " ", s)
    s = s.strip("\"'`\u201c\u201d\u2018\u2019")
    return s


def _is_unsafe(text: str) -> bool:
    return any(tok in text for tok in _INJECTION_TOKENS)


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pat, text, flags=re.IGNORECASE) for pat in patterns)


def _detect_roadmap_keyword(text: str) -> str | None:
    for kw in _ROADMAP_KEYWORDS:
        if kw in text:
            return kw
    return None


# ---------------------------------------------------------------------------
# Tiny8 CPU planning
# ---------------------------------------------------------------------------


def _plan_tiny8_cpu(raw_prompt: str, text: str) -> PlannerResult:
    """Build a Tiny8 CPU DesignIR.

    The default program is the "add 20 + 22 and halt" demo. If
    the prompt explicitly mentions adding, summing, or the
    numbers 20/22, we keep the default. Otherwise we surface a
    ClarificationRequest so the caller can either confirm the
    default or supply different operands.

    v1 does not parse arbitrary program semantics from the
    prompt — that is Phase C's assembler job. The planner only
    records the *intent* so the verification contract is right.
    """
    add_intent = _matches_any(text, _ADD_INTENT)

    if not add_intent:
        return ClarificationRequest(
            code=_REFUSAL_AMBIGUOUS_PROMPT,
            message=(
                "Recognised a Tiny8 CPU request but no program "
                "intent detected. The default program is "
                "'add 20 + 22 and halt'."
            ),
            question=("Which demo program should the generated CPU run?"),
            options=(
                "add 20 and 22 and halt (default)",
                "I'll provide a custom .asm",
            ),
            default="add 20 and 22 and halt",
            supported_kinds=("tiny8_cpu",),
        )

    return _build_tiny8_cpu_design(raw_prompt)


def _build_tiny8_cpu_design(raw_prompt: str) -> DesignIR:
    """Return the canonical Tiny8 CPU design for the v1 demo.

    The verification contract encodes the expected halt state so
    Phase C's testbench has a target.
    """
    return DesignIR(
        schemaVersion="0.1",
        domain="digital",
        kind="tiny8_cpu",
        name="tiny8_cpu_demo",
        description=(
            "Tiny8 CPU v1: 8-bit accumulator, 256-word ROM, "
            "256-byte RAM, 16-bit instructions, 15-opcode ISA, "
            "default program adds 20 and 22 and halts."
        ),
        clock=_default_clock(),
        reset=_default_reset(),
        cpu=CpuSpec(),
        memory=MemorySpec(),
        io=IoSpec(ports=[]),
        program=ProgramSpec(
            source=_DEFAULT_PROGRAM_SOURCE,
            entry=_DEFAULT_PROGRAM_ENTRY,
            expectedHaltCyclesMax=_DEFAULT_HALT_CYCLES,
        ),
        verification=VerificationSpec(
            expected=_default_expected_state(),
        ),
        metadata=Metadata(),
    )


# ---------------------------------------------------------------------------
# Tiny8 SoC planning (reserved)
# ---------------------------------------------------------------------------


def _plan_tiny8_soc(raw_prompt: str, text: str) -> PlannerResult:
    """Tiny8 SoC is reserved for Phase 12+1; v1 returns a roadmap."""
    return RoadmapSuggestion(
        code="ROADMAP_TINY8_SOC",
        message=("Tiny8 SoC (CPU + memory-mapped IO) is on the roadmap but not in v1."),
        category=_CATEGORY_TINY8_SOC,
        why_not_v1=(
            "v1 ships only the bare CPU. Memory-mapped IO, "
            "interrupt handling, and bus wrappers are scheduled "
            "after the v1 CPU is verified end-to-end."
        ),
        proposed_phases=(
            "phase-12.1: memory-mapped IO wrapper",
            "phase-12.2: simple bus + interrupt controller",
        ),
        next_step=(
            "For now, ask for a Tiny8 CPU instead, e.g. 'buat mini processor 8-bit sederhana'."
        ),
    )


# ---------------------------------------------------------------------------
# Roadmap builder
# ---------------------------------------------------------------------------


_WHY_NOT_V1: Final[str] = (
    "v1 is a verified 8-bit accumulator CPU (Tiny8). Full "
    "processor designs require an order-of-magnitude more work "
    "(compiler toolchain, bus architecture, memory hierarchy, "
    "peripherals, validation suite)."
)
_PROPOSED_PHASES: Final[tuple[str, ...]] = (
    "phase-12.1: Tiny8 SoC with memory-mapped IO",
    "phase-13: RV32I subset (compiler + simulator + tests)",
    "phase-14: full mini PC reference design (SoC + peripherals + boot ROM)",
)

_MINI_PC_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "mini pc",
        "pc lengkap",
        "full mini pc",
        "intel nuc",
        "raspberry pi",
        "motherboard",
    }
)


def _category_for_keyword(kw: str) -> str:
    return _CATEGORY_MINI_PC if kw in _MINI_PC_KEYWORDS else _CATEGORY_RISCV


_ROADMAP_TABLE: Final[Mapping[str, RoadmapSuggestion]] = {
    kw: RoadmapSuggestion(
        code=f"ROADMAP_{kw.upper().replace(' ', '_').replace('-', '_')}",
        message=(f"'{kw}' is a valid direction but not in the v1 Tiny8 scope."),
        category=_category_for_keyword(kw),
        why_not_v1=_WHY_NOT_V1,
        proposed_phases=_PROPOSED_PHASES,
        next_step=(
            "For v1, ask for a Tiny8 CPU instead, e.g. "
            "'buat mini processor 8-bit sederhana yang menjumlahkan "
            "20 dan 22'. The roadmap is documented in "
            "docs/digital/plan-tiny8-agent.md §12 (Phase F)."
        ),
    )
    for kw in _ROADMAP_KEYWORDS
}


def _build_roadmap(keyword: str, raw_prompt: str, text: str) -> RoadmapSuggestion:
    # Use the precomputed template; this gives every keyword the
    # same structured shape. Future per-keyword specialisation goes
    # here (e.g. different ``proposed_phases`` for RISC-V vs. full
    # mini PC).
    return _ROADMAP_TABLE[keyword]


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def _ambiguous_refusal(raw_prompt: str, text: str) -> PlannerRefusal:
    return PlannerRefusal(
        code=_REFUSAL_AMBIGUOUS_PROMPT,
        message=(
            "Prompt not recognised as a Tiny8 CPU or Tiny8 SoC "
            "request. v1 supports only the 8-bit accumulator CPU."
        ),
        supported_kinds=_SUPPORTED_KINDS,
        next_step=(
            "Provide a Tiny8 prompt such as 'buat mini processor "
            "8-bit sederhana' or 'create a tiny 8-bit CPU that "
            "adds two numbers and halts', or supply a Design IR "
            "JSON file via 'ltagent digital create <ir-file>'."
        ),
        data={"prompt": raw_prompt, "supportedKinds": list(_SUPPORTED_KINDS)},
    )


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def _default_clock() -> ClockSpec:
    return ClockSpec(name="clk", frequencyHz=_DEFAULT_CLOCK_HZ)


def _default_reset() -> ResetSpec:
    return ResetSpec(name="rst", activeHigh=True, synchronous=True)


def _default_expected_state() -> ExpectedState:
    return ExpectedState(
        halted=True,
        acc=_DEFAULT_DEMO_EXPECTED_ACC,
        memory={str(k): v for k, v in _DEFAULT_DEMO_EXPECTED_MEM.items()},
    )


# ---------------------------------------------------------------------------
# Convenience for the CLI / MCP layer
# ---------------------------------------------------------------------------


def default_demo_program() -> tuple[tuple[str, int], ...]:
    """Return the canonical v1 demo program. Used by tests and by
    the generator to seed ``demo.asm`` if the user did not provide
    a custom program source.
    """
    return _DEFAULT_DEMO_PROGRAM


__all__ = [
    "ClarificationRequest",
    "PlannerRefusal",
    "PlannerResult",
    "RoadmapSuggestion",
    "default_demo_program",
    "plan_digital_prompt",
]
