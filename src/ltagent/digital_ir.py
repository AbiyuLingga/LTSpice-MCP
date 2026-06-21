"""Design IR v0.1 for ltspice-ai-agent (Phase 12, Tiny8 CPU).

This module is the stable contract between AI intent and generated
digital artefacts. It defines Pydantic models, validation rules, and
structured errors for the Tiny8 CPU family. It is **separate** from
:mod:`ltagent.ir` (the analog ``CircuitIR``) because the schemas have
nothing in common beyond "they are JSON validated by pydantic".

Phase 12 scope:

- Load and validate ``DesignIR`` from JSON.
- Round-trip serialise back to JSON.
- Reject invalid IR with structured, actionable error codes.
- Export a JSON Schema (best-effort via pydantic).
- Two ``kind`` values: ``tiny8_cpu`` (CPU only) and ``tiny8_soc``
  (CPU + memory-mapped IO wrapper). v1 only ships ``tiny8_cpu``;
  ``tiny8_soc`` is reserved and must validate but is not yet
  generated.

Phase 12 does NOT include:

- Free-form HDL bodies inside the IR. The IR is structural;
  Verilog-2001 comes from ``ltagent.digital_templates`` only.
- LLM-based prompt expansion. The digital planner
  (``ltagent.digital_planner``) is rule-based like the analog
  planner.
- Path-bearing fields. The IR carries the program source
  *filename*; the workspace resolves the path and rejects
  traversal via ``ltagent.security``.

Security notes:

- All string fields use strict regexes; no SPICE-style injection
  vectors and no path separators.
- The IR is ``extra='forbid'``; unknown fields surface as
  structured errors, never silently ignored.
- Reserved opcode ``0xE`` is a semantic constraint, not a
  security one, but the assembler enforces it anyway.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

DESIGN_SCHEMA_VERSION = "0.1"
"""Only schema version currently accepted by the validator."""

# Supported design kinds for Phase 12. ``tiny8_soc`` is reserved and
# must validate, but no generator emits it yet. Adding a new kind
# requires updating this set, the templates, and the generator.
SUPPORTED_DESIGN_KINDS: frozenset[str] = frozenset({"tiny8_cpu", "tiny8_soc"})

# ISA identifiers recognised in v1. Tiny8 has one ISA ("tiny8_v0")
# that maps to the 16-instruction accumulator design.
SUPPORTED_ISA: frozenset[str] = frozenset({"tiny8_v0"})

# CPU architecture identifiers. v1 only ships "accumulator".
SUPPORTED_ARCHITECTURE: frozenset[str] = frozenset({"accumulator"})

# Direction enum for IO ports.
IO_DIRECTIONS: frozenset[str] = frozenset({"input", "output", "bidir"})

# Reasonable bounds for v1. Larger memories / wider data paths
# belong in a future ISA, not in this IR.
MIN_ADDRESS_WIDTH = 4
MAX_ADDRESS_WIDTH = 16
MIN_DATA_WIDTH = 4
MAX_DATA_WIDTH = 16
MIN_INSTRUCTION_WIDTH = 8
MAX_INSTRUCTION_WIDTH = 32
MIN_MEMORY_WORDS = 1
MAX_MEMORY_WORDS = 65536
MIN_HALT_CYCLES = 1
MAX_HALT_CYCLES = 1_000_000
MIN_PORT_WIDTH = 1
MAX_PORT_WIDTH = 64

# Program filename must be a relative, dot-relative path under the
# project. It must end with .asm and must not contain a path
# separator (the workspace resolves the project root, not the IR).
_PROGRAM_SOURCE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,127}\.asm$")

# Project name: same slug as the analog IR so a single naming rule
# covers both. Mirrors ``ir.py`` exactly.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

# Port name: short identifier used as a Verilog signal suffix.
# Lower-case + digits + underscores; must start with a letter.
_PORT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

# Identifier patterns for cpu sub-fields (clock/reset names).
_SIGNAL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class Direction(str, Enum):
    """IO port direction. String-valued for serialisation symmetry."""

    INPUT = "input"
    OUTPUT = "output"
    BIDIR = "bidir"


class IRError(BaseModel):
    """A single structured validation error for the design IR.

    The shape mirrors ``ltagent.ir.IRError`` so the CLI can render
    both kinds with the same code, so callers do not have to branch
    on domain.
    """

    model_config = ConfigDict(extra="forbid")

    code: str
    path: str
    detail: str


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class ClockSpec(BaseModel):
    """Clock port definition."""

    model_config = ConfigDict(extra="forbid")

    name: str = "clk"
    frequencyHz: int = Field(default=1_000_000, ge=1, le=10_000_000_000)

    @field_validator("name")
    @classmethod
    def _name_safe(cls, v: str) -> str:
        if not _SIGNAL_NAME_RE.match(v):
            raise ValueError(f"clock name {v!r} must match {_SIGNAL_NAME_RE.pattern}")
        return v


class ResetSpec(BaseModel):
    """Reset port definition."""

    model_config = ConfigDict(extra="forbid")

    name: str = "rst"
    activeHigh: bool = True
    synchronous: bool = True

    @field_validator("name")
    @classmethod
    def _name_safe(cls, v: str) -> str:
        if not _SIGNAL_NAME_RE.match(v):
            raise ValueError(f"reset name {v!r} must match {_SIGNAL_NAME_RE.pattern}")
        return v


class CpuSpec(BaseModel):
    """CPU datapath widths and ISA selection."""

    model_config = ConfigDict(extra="forbid")

    dataWidth: int = 8
    addressWidth: int = 8
    instructionWidth: int = 16
    architecture: str = "accumulator"
    isa: str = "tiny8_v0"

    @field_validator("dataWidth")
    @classmethod
    def _data_width(cls, v: int) -> int:
        if not MIN_DATA_WIDTH <= v <= MAX_DATA_WIDTH:
            raise ValueError(f"dataWidth must be in [{MIN_DATA_WIDTH}, {MAX_DATA_WIDTH}]")
        return v

    @field_validator("addressWidth")
    @classmethod
    def _address_width(cls, v: int) -> int:
        if not MIN_ADDRESS_WIDTH <= v <= MAX_ADDRESS_WIDTH:
            raise ValueError(f"addressWidth must be in [{MIN_ADDRESS_WIDTH}, {MAX_ADDRESS_WIDTH}]")
        return v

    @field_validator("instructionWidth")
    @classmethod
    def _instruction_width(cls, v: int) -> int:
        if not MIN_INSTRUCTION_WIDTH <= v <= MAX_INSTRUCTION_WIDTH:
            raise ValueError(
                f"instructionWidth must be in [{MIN_INSTRUCTION_WIDTH}, {MAX_INSTRUCTION_WIDTH}]"
            )
        return v

    @field_validator("architecture")
    @classmethod
    def _architecture(cls, v: str) -> str:
        if v not in SUPPORTED_ARCHITECTURE:
            raise ValueError(
                f"architecture {v!r} not supported; allowed: {sorted(SUPPORTED_ARCHITECTURE)}"
            )
        return v

    @field_validator("isa")
    @classmethod
    def _isa(cls, v: str) -> str:
        if v not in SUPPORTED_ISA:
            raise ValueError(f"isa {v!r} not supported; allowed: {sorted(SUPPORTED_ISA)}")
        return v


class MemorySpec(BaseModel):
    """Memory sizes for program ROM and data RAM."""

    model_config = ConfigDict(extra="forbid")

    romWords: int = 256
    ramBytes: int = 256

    @field_validator("romWords")
    @classmethod
    def _rom(cls, v: int) -> int:
        if not MIN_MEMORY_WORDS <= v <= MAX_MEMORY_WORDS:
            raise ValueError(f"romWords must be in [{MIN_MEMORY_WORDS}, {MAX_MEMORY_WORDS}]")
        return v

    @field_validator("ramBytes")
    @classmethod
    def _ram(cls, v: int) -> int:
        if not MIN_MEMORY_WORDS <= v <= MAX_MEMORY_WORDS:
            raise ValueError(f"ramBytes must be in [{MIN_MEMORY_WORDS}, {MAX_MEMORY_WORDS}]")
        return v


class IoPort(BaseModel):
    """A single IO port on the CPU."""

    model_config = ConfigDict(extra="forbid")

    name: str
    direction: Direction
    width: int = 8

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not _PORT_NAME_RE.match(v):
            raise ValueError(f"port name {v!r} must match {_PORT_NAME_RE.pattern}")
        return v

    @field_validator("width")
    @classmethod
    def _width(cls, v: int) -> int:
        if not MIN_PORT_WIDTH <= v <= MAX_PORT_WIDTH:
            raise ValueError(f"port width must be in [{MIN_PORT_WIDTH}, {MAX_PORT_WIDTH}]")
        return v


class IoSpec(BaseModel):
    """IO port list."""

    model_config = ConfigDict(extra="forbid")

    ports: list[IoPort] = Field(default_factory=list)

    @field_validator("ports")
    @classmethod
    def _ports_unique(cls, v: list[IoPort]) -> list[IoPort]:
        seen: set[str] = set()
        for p in v:
            if p.name in seen:
                raise ValueError(f"io port {p.name!r} is duplicated")
            seen.add(p.name)
        return v


class ProgramSpec(BaseModel):
    """The assembler program bound to this design."""

    model_config = ConfigDict(extra="forbid")

    source: str
    entry: int = 0
    expectedHaltCyclesMax: int = 200

    @field_validator("source")
    @classmethod
    def _source(cls, v: str) -> str:
        if not _PROGRAM_SOURCE_RE.match(v):
            raise ValueError(
                f"program.source {v!r} must be a relative path ending in .asm with no traversal"
            )
        return v

    @field_validator("entry")
    @classmethod
    def _entry(cls, v: int) -> int:
        if v < 0:
            raise ValueError("program.entry must be >= 0")
        return v

    @field_validator("expectedHaltCyclesMax")
    @classmethod
    def _halt(cls, v: int) -> int:
        if not MIN_HALT_CYCLES <= v <= MAX_HALT_CYCLES:
            raise ValueError(
                f"expectedHaltCyclesMax must be in [{MIN_HALT_CYCLES}, {MAX_HALT_CYCLES}]"
            )
        return v


class ExpectedState(BaseModel):
    """What the testbench should observe after halt.

    ``memory`` is a flat ``dict[str, int]`` on the wire so the plan
    doc shape ``"memory": {"16": 42}`` round-trips naturally. The
    model validator below enforces the key/value bounds.
    """

    model_config = ConfigDict(extra="forbid")

    halted: bool = True
    acc: int | None = None
    memory: dict[str, int] = Field(default_factory=dict)

    @field_validator("acc")
    @classmethod
    def _acc_range(cls, v: int | None) -> int | None:
        if v is None:
            return v
        if v < 0 or v > 0xFF:
            raise ValueError("verification.expected.acc must be in 0..255")
        return v

    @model_validator(mode="after")
    def _check_memory(self) -> ExpectedState:
        for k, v in list(self.memory.items()):
            if not re.fullmatch(r"\d+", k):
                raise ValueError(
                    f"verification.expected.memory key {k!r} must be a non-negative integer string"
                )
            addr = int(k)
            if addr > MAX_MEMORY_WORDS:
                raise ValueError(
                    f"verification.expected.memory key {k!r} is out of range 0..{MAX_MEMORY_WORDS}"
                )
            if v < 0 or v > 0xFF:
                raise ValueError(f"verification.expected.memory[{k}] = {v} must be in 0..255")
        return self

    def memory_as_int_dict(self) -> dict[int, int]:
        """Return a copy of ``memory`` with int keys for comparison."""
        return {int(k): v for k, v in self.memory.items()}


class VerificationSpec(BaseModel):
    """The testbench verification contract."""

    model_config = ConfigDict(extra="forbid")

    expected: ExpectedState = Field(default_factory=ExpectedState)


class ArtifactLists(BaseModel):
    """File list placeholders. The generator fills these on write."""

    model_config = ConfigDict(extra="forbid")

    rtl: list[str] = Field(default_factory=list)
    testbenches: list[str] = Field(default_factory=list)
    reports: list[str] = Field(default_factory=list)


class Metadata(BaseModel):
    """Provenance metadata."""

    model_config = ConfigDict(extra="forbid")

    createdBy: str = "ltagent"
    source: str = "digital_planner"

    @field_validator("createdBy")
    @classmethod
    def _creator(cls, v: str) -> str:
        if not v or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", v):
            raise ValueError("metadata.createdBy is not a safe identifier")
        return v

    @field_validator("source")
    @classmethod
    def _source(cls, v: str) -> str:
        if not v or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", v):
            raise ValueError("metadata.source is not a safe identifier")
        return v


class DesignIR(BaseModel):
    """Top-level Design IR v0.1 for Phase 12 (Tiny8 CPU).

    This is **separate** from ``CircuitIR``. The two IRs share no
    field names except ``schemaVersion``, ``name``, ``description``,
    and ``metadata``. Mixing them up is a bug; the package-level
    imports make the boundary obvious.
    """

    model_config = ConfigDict(extra="forbid")

    schemaVersion: str = DESIGN_SCHEMA_VERSION
    domain: str = "digital"
    kind: str
    name: str
    description: str = ""
    clock: ClockSpec = Field(default_factory=lambda: ClockSpec())
    reset: ResetSpec = Field(default_factory=lambda: ResetSpec())
    cpu: CpuSpec = Field(default_factory=lambda: CpuSpec())
    memory: MemorySpec = Field(default_factory=lambda: MemorySpec())
    io: IoSpec = Field(default_factory=lambda: IoSpec())
    program: ProgramSpec
    verification: VerificationSpec = Field(default_factory=lambda: VerificationSpec())
    artifacts: ArtifactLists = Field(default_factory=lambda: ArtifactLists())
    metadata: Metadata = Field(default_factory=lambda: Metadata())

    @field_validator("schemaVersion")
    @classmethod
    def _version(cls, v: str) -> str:
        if v != DESIGN_SCHEMA_VERSION:
            raise ValueError(
                f"schemaVersion {v!r} not supported; only {DESIGN_SCHEMA_VERSION!r} is accepted"
            )
        return v

    @field_validator("domain")
    @classmethod
    def _domain(cls, v: str) -> str:
        if v != "digital":
            raise ValueError(f"domain {v!r} is not 'digital'; this is the digital IR")
        return v

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in SUPPORTED_DESIGN_KINDS:
            raise ValueError(
                f"design kind {v!r} not supported; allowed: {sorted(SUPPORTED_DESIGN_KINDS)}"
            )
        return v

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(f"design name {v!r} must match {_NAME_RE.pattern}")
        return v

    @field_validator("description")
    @classmethod
    def _description(cls, v: str) -> str:
        if len(v) > 1024:
            raise ValueError("description is longer than 1024 chars")
        # Reject newlines to keep generated headers single-line.
        if "\n" in v or "\r" in v:
            raise ValueError("description must not contain newlines")
        return v

    @model_validator(mode="after")
    def _consistency(self) -> DesignIR:
        # Tiny8 v0 is fixed at 8/8/16. Future ISAs can lift this.
        if self.cpu.isa == "tiny8_v0" and (
            self.cpu.dataWidth != 8 or self.cpu.addressWidth != 8 or self.cpu.instructionWidth != 16
        ):
            raise ValueError(
                "isa 'tiny8_v0' requires dataWidth=8, addressWidth=8, instructionWidth=16"
            )
        # Program ROM size must be expressible in addressWidth bits.
        if self.memory.romWords > (1 << self.cpu.addressWidth):
            raise ValueError(
                f"romWords {self.memory.romWords} exceeds address space "
                f"2**{self.cpu.addressWidth} = {1 << self.cpu.addressWidth}"
            )
        if self.memory.ramBytes > (1 << self.cpu.addressWidth):
            raise ValueError(
                f"ramBytes {self.memory.ramBytes} exceeds address space "
                f"2**{self.cpu.addressWidth} = {1 << self.cpu.addressWidth}"
            )
        return self


# ---------------------------------------------------------------------------
# IO helpers (mirror the analog IR's API for CLI parity)
# ---------------------------------------------------------------------------


def load_design(source: str | Path | dict[str, Any]) -> DesignIR:
    """Load and validate a Design IR from a file path, JSON string, or dict.

    Raises:
        FileNotFoundError: if source is a path that does not exist.
        json.JSONDecodeError: if the JSON is malformed.
        pydantic.ValidationError: if validation fails. Callers in higher
            layers should format this with :func:`format_errors`.
    """
    if isinstance(source, dict):
        data = source
    elif isinstance(source, (str, Path)):
        path = Path(source)
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    else:
        raise TypeError(f"unsupported source type: {type(source).__name__}")
    return DesignIR.model_validate(data)


def dump_design(ir: DesignIR, indent: int | None = 2) -> str:
    """Serialize a DesignIR back to JSON string with stable key order."""
    return ir.model_dump_json(indent=indent, by_alias=False)


def validate_dict(data: Mapping[str, Any]) -> DesignIR:
    """Validate a Python dict as a DesignIR; convenience for callers
    that have already parsed JSON.
    """
    return DesignIR.model_validate(dict(data))


def format_errors(exc: Exception) -> list[IRError]:
    """Convert a pydantic ValidationError into a list of IRError.

    The CLI layer renders this directly into the JSON contract.
    Codes are stable identifiers that other agents can switch on.
    """
    from pydantic import ValidationError  # local import to avoid cycle

    if not isinstance(exc, ValidationError):
        return [
            IRError(
                code="DESIGN_INTERNAL_ERROR",
                path="<root>",
                detail=str(exc),
            )
        ]

    errors: list[IRError] = []
    for err_dict in exc.errors():
        loc = ".".join(str(p) for p in err_dict.get("loc", ())) or "<root>"
        etype = err_dict.get("type", "validation_error")
        msg = err_dict.get("msg", "invalid value")
        errors.append(
            IRError(
                code=_pydantic_type_to_code(etype, loc),
                path=loc,
                detail=msg,
            )
        )
    return errors


def _pydantic_type_to_code(etype: str, path: str) -> str:
    """Map a pydantic error to a stable ``DESIGN_*`` code.

    The custom field validators in this module all raise
    ``ValueError``, which pydantic surfaces as ``value_error`` with
    the offending field in ``loc``. So the path is the primary key
    and the error type is only used for the special cases
    (``missing``, ``extra_forbidden``).

    Code namespaces:
      DESIGN_SCHEMA_*   schema-level rejection (version, kind, name)
      DESIGN_DOMAIN_*   domain mismatch
      DESIGN_CPU_*      CPU spec rejection
      DESIGN_MEM_*      memory spec rejection
      DESIGN_IO_*       IO port rejection
      DESIGN_PROG_*     program spec rejection
      DESIGN_VERIFY_*   verification spec rejection
      DESIGN_META_*     metadata rejection
      DESIGN_EXTRA_*    unknown field
      DESIGN_INTERNAL_* fallback
    """
    # 1. Extra field is the only error type that has a unique shape
    #    and is not path-driven.
    if etype == "extra_forbidden":
        return "DESIGN_EXTRA_FIELD"

    # 2. The full dotted path is the primary key. Order matters: more
    #    specific paths first so they win over the head match below.
    by_path = {
        "schemaVersion": "DESIGN_SCHEMA_UNSUPPORTED_VERSION",
        "domain": "DESIGN_DOMAIN_INVALID",
        "name": "DESIGN_SCHEMA_BAD_NAME",
        "description": "DESIGN_SCHEMA_BAD_DESCRIPTION",
        "kind": "DESIGN_SCHEMA_BAD_KIND",
        "cpu.dataWidth": "DESIGN_CPU_DATA_WIDTH",
        "cpu.addressWidth": "DESIGN_CPU_ADDRESS_WIDTH",
        "cpu.instructionWidth": "DESIGN_CPU_INSTRUCTION_WIDTH",
        "cpu.architecture": "DESIGN_CPU_ARCHITECTURE",
        "cpu.isa": "DESIGN_CPU_ISA",
        "memory.romWords": "DESIGN_MEM_ROM_WORDS",
        "memory.ramBytes": "DESIGN_MEM_RAM_BYTES",
        "io.ports": "DESIGN_IO_PORT_DUPLICATE",
        "clock.frequencyHz": "DESIGN_CLOCK_FREQUENCY",
        "clock.name": "DESIGN_CLOCK_NAME",
        "reset.name": "DESIGN_RESET_NAME",
        "program.source": "DESIGN_PROG_SOURCE",
        "program.entry": "DESIGN_PROG_ENTRY",
        "program.expectedHaltCyclesMax": "DESIGN_PROG_HALT_CYCLES",
        "verification.expected.acc": "DESIGN_VERIFY_ACC_RANGE",
        "verification.expected.memory": "DESIGN_VERIFY_MEMORY_RANGE",
        "metadata.createdBy": "DESIGN_META_CREATOR",
        "metadata.source": "DESIGN_META_SOURCE",
    }
    if path in by_path:
        return by_path[path]
    if etype == "missing":
        return "DESIGN_MISSING_FIELD"
    return "DESIGN_VALUE_ERROR"


__all__ = [
    "DESIGN_SCHEMA_VERSION",
    "IO_DIRECTIONS",
    "SUPPORTED_ARCHITECTURE",
    "SUPPORTED_DESIGN_KINDS",
    "SUPPORTED_ISA",
    "ArtifactLists",
    "ClockSpec",
    "CpuSpec",
    "DesignIR",
    "Direction",
    "ExpectedState",
    "IRError",
    "IoPort",
    "IoSpec",
    "MemorySpec",
    "Metadata",
    "ProgramSpec",
    "ResetSpec",
    "VerificationSpec",
    "dump_design",
    "format_errors",
    "load_design",
    "validate_dict",
]
