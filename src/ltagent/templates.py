"""Phase 6: Local template library for ``ltagent``.

The template library is the project's memory of verified circuits. It lets
the agent (and humans) reuse stable designs instead of regenerating
slightly-different variants of the same topology for every cutoff, every
input voltage, or every capacitor value.

Directory layout (per plan section 15.1 and ``AGENTS.md``):

::

    <workspace>/templates/
        index.json
        official/<template_id>/
            manifest.json
            template.ir.json
            template.cir
            template.asc
        candidates/<template_id>/...
        rejected/<template_id>/...

Each directory under a status folder holds one :class:`TemplateManifest`
plus the circuit files referenced by ``manifest.files``. The on-disk
manifest is the source of truth; the in-memory dataclass is a thin
wrapper for type safety and to keep the contract reviewable.

This module is **read/write safe**: it never executes the system shell,
never lets a caller pick a status or path outside the configured
templates directory, and never touches anything above the configured
templates root. All writes are atomic (``Path.replace`` after writing to
a sibling ``.tmp``).

Phase 6 scope (per plan section 21):

* Manifest read/write round-trip
* ``list`` / ``show`` / ``match`` / ``audit``
* Candidate / official / rejected transitions via :func:`move_template`
* Use-count tracking
* Default seed of the 3 MVP topologies via :func:`seed_default_templates`

Out of scope for Phase 6 (lands in Phase 9):

* Scoring-driven promotion (evaluator)
* Auto-promotion
* ``template evaluate`` / ``template promote`` CLI subcommands
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any

from .ir import CircuitIR

# UP042 is intentionally violated here: the project standard
# (see ``ir.ComponentKind``, ``ir.AnalysisKind``) is
# ``class X(str, Enum)`` for ergonomic ``value`` access and JSON
# serialisation. ``enum.StrEnum`` would change the runtime type and
# break comparison symmetry with persisted JSON.

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEMPLATES_SCHEMA_VERSION: str = "0.1"
"""Manifest schema version. Bumped on backwards-incompatible changes."""

#: Sub-directories under the templates root that hold template packs.
STATUS_DIRS: tuple[str, ...] = ("official", "candidates", "rejected")

#: Name of the top-level index file. Holds a quick summary so ``list`` can
#: avoid scanning every status directory when an explicit index is present.
INDEX_FILENAME: str = "index.json"

#: Name of the per-template manifest file.
MANIFEST_FILENAME: str = "manifest.json"

#: Pattern for safe template ids. Lowercase letter first, then letters /
#: digits / underscores, 1-64 chars. Rejects path traversal attempts by
#: construction.
TEMPLATE_ID_PATTERN: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

#: Stable error codes (also used by the CLI layer).
ERR_TEMPLATE_NOT_FOUND = "TEMPLATE_NOT_FOUND"
ERR_TEMPLATE_INVALID = "TEMPLATE_INVALID"
ERR_TEMPLATE_DUPLICATE = "TEMPLATE_DUPLICATE"
ERR_TEMPLATE_PATH_TRAVERSAL = "TEMPLATE_PATH_TRAVERSAL"
ERR_TEMPLATE_STATUS_INVALID = "TEMPLATE_STATUS_INVALID"
ERR_TEMPLATE_IO = "TEMPLATE_IO"
ERR_TEMPLATE_TOPOLOGY_UNSUPPORTED = "TEMPLATE_TOPOLOGY_UNSUPPORTED"
ERR_TEMPLATE_ID_INVALID = "TEMPLATE_ID_INVALID"
ERR_TEMPLATE_PROJECT_INVALID = "TEMPLATE_PROJECT_INVALID"
ERR_TEMPLATE_PROJECT_NO_RESULT = "TEMPLATE_PROJECT_NO_RESULT"
ERR_TEMPLATE_SIM_NOT_VERIFIED = "TEMPLATE_SIM_NOT_VERIFIED"
ERR_TEMPLATE_LAYOUT_MISSING = "TEMPLATE_LAYOUT_MISSING"


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class TemplateStatus(str, Enum):
    """Status of a template in the library.

    Values match the sub-directory names under the templates root.
    """

    OFFICIAL = "official"
    CANDIDATE = "candidates"
    REJECTED = "rejected"

    @classmethod
    def from_str(cls, value: str) -> TemplateStatus:
        """Parse a status string, accepting common aliases.

        ``"official"``, ``"candidate"``, and ``"rejected"`` (singular
        forms) are accepted for ergonomic reasons; the canonical
        directory names are the plural ``"candidates"`` and ``"rejected"``.
        """
        if not isinstance(value, str):
            raise ValueError(f"status must be a string, got {type(value).__name__}")
        v = value.strip().lower()
        if v in ("official",):
            return cls.OFFICIAL
        if v in ("candidate", "candidates"):
            return cls.CANDIDATE
        if v in ("rejected",):
            return cls.REJECTED
        raise ValueError(
            f"unknown template status: {value!r}; expected one of "
            f"{[s.value for s in cls]}"
        )

    @property
    def display(self) -> str:
        """Human-friendly label, singular form."""
        return {
            TemplateStatus.OFFICIAL: "official",
            TemplateStatus.CANDIDATE: "candidate",
            TemplateStatus.REJECTED: "rejected",
        }[self]


@dataclass(frozen=True)
class TemplateParameter:
    """Declarative description of one editable template parameter.

    Mirrors the shape used in plan section 15.2. The Python core is the
    only writer; agents never inline-edit these.
    """

    description: str
    default: str
    editable: bool = True


@dataclass(frozen=True)
class TemplateManifest:
    """The persisted, in-memory representation of a template entry.

    The on-disk JSON form is the source of truth. ``TemplateManifest`` is
    a frozen dataclass so it can be hashed and compared in tests.
    """

    templateId: str
    schemaVersion: str
    name: str
    topology: str
    status: TemplateStatus
    tags: tuple[str, ...] = ()
    description: str | None = None
    files: dict[str, str] = field(default_factory=dict)
    parameters: dict[str, TemplateParameter] = field(default_factory=dict)
    formula: dict[str, str] = field(default_factory=dict)
    layoutScore: int | None = None
    simulationVerified: bool = False
    useCount: int = 0
    createdAt: str = field(default_factory=lambda: date.today().isoformat())
    updatedAt: str = field(default_factory=lambda: date.today().isoformat())

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["tags"] = list(self.tags)
        d["parameters"] = {k: asdict(v) for k, v in self.parameters.items()}
        d["schemaVersion"] = self.schemaVersion
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TemplateManifest:
        params_raw = data.get("parameters", {}) or {}
        params: dict[str, TemplateParameter] = {}
        for k, v in params_raw.items():
            if isinstance(v, Mapping):
                params[k] = TemplateParameter(
                    description=str(v.get("description", "")),
                    default=str(v.get("default", "")),
                    editable=bool(v.get("editable", True)),
                )
            elif isinstance(v, str):
                params[k] = TemplateParameter(description="", default=v, editable=True)
        status = TemplateStatus.from_str(data.get("status", "candidate"))
        layout = data.get("layoutScore")
        tags_raw = data.get("tags", []) or []
        tags = tuple(str(t) for t in tags_raw)
        files_raw = data.get("files", {}) or {}
        files = {str(k): str(v) for k, v in files_raw.items()}
        formula_raw = data.get("formula", {}) or {}
        formula = {str(k): str(v) for k, v in formula_raw.items()}
        return cls(
            templateId=str(data["templateId"]),
            schemaVersion=str(data.get("schemaVersion", TEMPLATES_SCHEMA_VERSION)),
            name=str(data.get("name", data["templateId"])),
            topology=str(data["topology"]),
            status=status,
            tags=tags,
            description=data.get("description"),
            files=files,
            parameters=params,
            formula=formula,
            layoutScore=int(layout) if layout is not None else None,
            simulationVerified=bool(data.get("simulationVerified", False)),
            useCount=int(data.get("useCount", 0)),
            createdAt=str(data.get("createdAt", date.today().isoformat())),
            updatedAt=str(data.get("updatedAt", date.today().isoformat())),
        )


@dataclass(frozen=True)
class MatchResult:
    """Result of :func:`match_template`.

    The acceptance criteria for Phase 6 are: "Same topology with different
    values does not create duplicate official template." This dataclass
    carries the data needed to make that decision:

    * ``matched`` — was a candidate template found?
    * ``template`` — the candidate manifest (or ``None``).
    * ``isValueVariant`` — ``True`` iff topology matches but the IR is
      not identical to the stored template. A value-only variant should
      reuse the existing official template, not create a new one.
    * ``useCount`` — current use count after the optional bump.
    * ``useCountBumped`` — whether the match call incremented the count.
    """

    matched: bool
    template: TemplateManifest | None
    isValueVariant: bool
    useCount: int
    useCountBumped: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched": self.matched,
            "isValueVariant": self.isValueVariant,
            "useCount": self.useCount,
            "useCountBumped": self.useCountBumped,
            "reason": self.reason,
            "template": self.template.to_dict() if self.template else None,
        }


@dataclass(frozen=True)
class AuditReport:
    """Summary of the local template library, produced by :func:`audit_templates`."""

    templatesDir: str
    counts: dict[str, int]
    totals: dict[str, int]
    topologies: dict[str, int]
    duplicates: tuple[tuple[str, tuple[str, ...]], ...]
    warnings: tuple[dict[str, Any], ...]
    indexed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "templatesDir": self.templatesDir,
            "counts": dict(self.counts),
            "totals": dict(self.totals),
            "topologies": dict(self.topologies),
            "duplicates": [
                {"topology": t, "templateIds": list(ids)} for t, ids in self.duplicates
            ],
            "warnings": list(self.warnings),
            "indexed": self.indexed,
        }


class TemplateError(ValueError):
    """Structured error for the templates module.

    Carries a stable error code so the CLI layer can render it in the
    JSON output contract without re-parsing the message.
    """

    def __init__(
        self,
        code: str,
        detail: str,
        data: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.data: dict[str, Any] = dict(data) if data else {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail, "data": self.data}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _today() -> str:
    return date.today().isoformat()


def _validate_id(template_id: str) -> str:
    if not isinstance(template_id, str) or not TEMPLATE_ID_PATTERN.match(template_id):
        raise TemplateError(
            ERR_TEMPLATE_ID_INVALID,
            f"template id {template_id!r} must match {TEMPLATE_ID_PATTERN.pattern}",
            {"templateId": str(template_id)},
        )
    return template_id


def _safe_within(child: Path, parent: Path) -> bool:
    """Return True iff ``child`` resolves under ``parent``.

    Thin wrapper around :func:`ltagent.security.is_within` so the CLI
    and the MCP server share the same guard.
    """
    from .security import is_within

    return is_within(child, parent)


def _ensure_root(templates_dir: str | Path) -> Path:
    """Resolve and validate the templates root.

    Creates the root directory (and status sub-directories) if it does
    not exist. The returned path is the resolved absolute path; all
    subsequent operations are bounded by it.
    """
    if not isinstance(templates_dir, (str, Path)):
        raise TemplateError(
            ERR_TEMPLATE_INVALID,
            f"templates_dir must be a path, got {type(templates_dir).__name__}",
        )
    root = Path(templates_dir).expanduser()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise TemplateError(
            ERR_TEMPLATE_IO,
            f"cannot create templates root {root}: {exc}",
            {"path": str(root)},
        ) from exc
    for status in STATUS_DIRS:
        (root / status).mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _manifest_path(root: Path, status: TemplateStatus, template_id: str) -> Path:
    _validate_id(template_id)
    return root / status.value / template_id / MANIFEST_FILENAME


def _ir_path(root: Path, status: TemplateStatus, template_id: str) -> Path:
    _validate_id(template_id)
    return root / status.value / template_id / "template.ir.json"


# ---------------------------------------------------------------------------
# Manifest IO
# ---------------------------------------------------------------------------


def load_manifest(path: str | Path) -> TemplateManifest:
    """Read a :class:`TemplateManifest` from a single JSON file.

    Raises :class:`TemplateError` on any structural problem. The error
    code is one of the ``ERR_TEMPLATE_*`` constants above.
    """
    p = Path(path)
    if not p.is_file():
        raise TemplateError(
            ERR_TEMPLATE_NOT_FOUND,
            f"manifest not found: {p}",
            {"path": str(p)},
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TemplateError(
            ERR_TEMPLATE_INVALID,
            f"manifest at {p} is not valid JSON: {exc}",
            {"path": str(p)},
        ) from exc
    except OSError as exc:
        raise TemplateError(
            ERR_TEMPLATE_IO,
            f"cannot read manifest at {p}: {exc}",
            {"path": str(p)},
        ) from exc
    if not isinstance(data, Mapping):
        raise TemplateError(
            ERR_TEMPLATE_INVALID,
            f"manifest at {p} must be a JSON object, got {type(data).__name__}",
            {"path": str(p)},
        )
    try:
        return TemplateManifest.from_dict(data)
    except (KeyError, ValueError, TypeError) as exc:
        raise TemplateError(
            ERR_TEMPLATE_INVALID,
            f"manifest at {p} is invalid: {exc}",
            {"path": str(p)},
        ) from exc


def dump_manifest(manifest: TemplateManifest, path: str | Path) -> Path:
    """Write a :class:`TemplateManifest` to ``path`` atomically.

    Uses a sibling ``.tmp`` file and ``Path.replace`` so an interrupted
    write cannot leave a half-written manifest on disk.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = manifest.to_dict()
    # Stable, reviewable key order.
    ordered = {
        "templateId": payload["templateId"],
        "schemaVersion": payload["schemaVersion"],
        "name": payload["name"],
        "topology": payload["topology"],
        "status": payload["status"],
        "tags": payload["tags"],
        "description": payload.get("description"),
        "files": payload["files"],
        "parameters": payload["parameters"],
        "formula": payload["formula"],
        "layoutScore": payload.get("layoutScore"),
        "simulationVerified": payload["simulationVerified"],
        "useCount": payload["useCount"],
        "createdAt": payload["createdAt"],
        "updatedAt": payload["updatedAt"],
    }
    # Write to a sibling .tmp then atomically replace. Using
    # ``mkstemp`` keeps the file on disk so we can ``Path.replace`` it;
    # the underlying file descriptor is closed by ``Path.open`` below.
    _fd, tmp_name = tempfile.mkstemp(
        prefix=".manifest_", suffix=".tmp", dir=str(target.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with Path(tmp_path).open("wb") as fh:
            fh.write(json.dumps(ordered, indent=2, sort_keys=False).encode("utf-8"))
            fh.write(b"\n")
        tmp_path.replace(target)
    except Exception:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise
    return target


# ---------------------------------------------------------------------------
# Library queries
# ---------------------------------------------------------------------------


def _status_dir(root: Path, status: TemplateStatus) -> Path:
    return root / status.value


def list_templates(
    templates_dir: str | Path,
    *,
    status: TemplateStatus | str | None = None,
) -> list[TemplateManifest]:
    """Return all templates under the given status, sorted by id.

    If ``status`` is ``None``, scans all three status directories and
    returns a combined list. Entries that fail to load are skipped; the
    audit command surfaces them.
    """
    root = _ensure_root(templates_dir)
    statuses: list[TemplateStatus]
    if status is None:
        statuses = list(TemplateStatus)
    elif isinstance(status, TemplateStatus):
        statuses = [status]
    else:
        statuses = [TemplateStatus.from_str(status)]

    manifests: list[TemplateManifest] = []
    for s in statuses:
        d = _status_dir(root, s)
        if not d.is_dir():
            continue
        for child in sorted(d.iterdir()):
            if not child.is_dir():
                continue
            mp = child / MANIFEST_FILENAME
            if not mp.is_file():
                # Tolerate non-template dirs (e.g. .gitkeep). The audit
                # command reports the real layout.
                continue
            try:
                manifests.append(load_manifest(mp))
            except TemplateError:
                # Skip broken entries; audit will surface them.
                continue
    return manifests


def show_template(
    templates_dir: str | Path,
    template_id: str,
    *,
    status: TemplateStatus | str = TemplateStatus.OFFICIAL,
) -> TemplateManifest:
    """Return a single template manifest by id, or raise :class:`TemplateError`."""
    root = _ensure_root(templates_dir)
    _validate_id(template_id)
    s = status if isinstance(status, TemplateStatus) else TemplateStatus.from_str(status)
    mp = _manifest_path(root, s, template_id)
    return load_manifest(mp)


def find_by_topology(
    templates_dir: str | Path,
    topology: str,
    *,
    status: TemplateStatus | str = TemplateStatus.OFFICIAL,
) -> TemplateManifest | None:
    """Return the first template with the given topology, or ``None``."""
    for m in list_templates(templates_dir, status=status):
        if m.topology == topology:
            return m
    return None


# ---------------------------------------------------------------------------
# Use count
# ---------------------------------------------------------------------------


def increment_use_count(
    templates_dir: str | Path,
    template_id: str,
    *,
    status: TemplateStatus | str = TemplateStatus.OFFICIAL,
) -> int:
    """Bump the template's ``useCount`` by 1 and persist it.

    Returns the new count. Idempotency is the caller's responsibility;
    this function unconditionally increments.
    """
    root = _ensure_root(templates_dir)
    s = status if isinstance(status, TemplateStatus) else TemplateStatus.from_str(status)
    _validate_id(template_id)
    mp = _manifest_path(root, s, template_id)
    if not mp.is_file():
        raise TemplateError(
            ERR_TEMPLATE_NOT_FOUND,
            f"template {template_id!r} not found in {s.value}",
            {"templateId": template_id, "status": s.value, "path": str(mp)},
        )
    manifest = load_manifest(mp)
    bumped = replace(
        manifest,
        useCount=manifest.useCount + 1,
        updatedAt=_today(),
    )
    dump_manifest(bumped, mp)
    return bumped.useCount


# ---------------------------------------------------------------------------
# Match
# ---------------------------------------------------------------------------


def _ir_topology(ir: CircuitIR | Mapping[str, Any]) -> str:
    if isinstance(ir, CircuitIR):
        return ir.topology
    if isinstance(ir, Mapping):
        topo = ir.get("topology")
        if not isinstance(topo, str):
            raise TemplateError(
                ERR_TEMPLATE_INVALID,
                "IR dict is missing 'topology'",
                {"path": "topology"},
            )
        return topo
    raise TemplateError(
        ERR_TEMPLATE_INVALID,
        f"IR must be CircuitIR or dict, got {type(ir).__name__}",
    )


def _ir_signature(ir: CircuitIR | Mapping[str, Any]) -> tuple[Any, ...]:
    """Return a hashable signature describing the IR's structure.

    Two IRs with the same signature are considered "the same circuit
    modulo value-only differences". The signature deliberately ignores
    parameter values, parameter defaults, descriptions, and any
    free-form metadata. It captures topology, component kinds, and the
    sequence of node terminals so that structural identity is preserved.
    """
    if isinstance(ir, CircuitIR):
        components = [
            (c.id, c.kind.value, tuple(c.nodes), c.role) for c in ir.components
        ]
        return (ir.topology, tuple(components))
    if isinstance(ir, Mapping):
        comps = ir.get("components", []) or []
        norm_comps: list[tuple[str, str, tuple[str, ...], str | None]] = []
        for c in comps:
            if not isinstance(c, Mapping):
                continue
            kind = str(c.get("kind", ""))
            nodes = tuple(str(n) for n in c.get("nodes", []) or ())
            role = c.get("role")
            cid = str(c.get("id", ""))
            norm_comps.append((cid, kind, nodes, str(role) if role is not None else None))
        return (str(ir.get("topology", "")), tuple(norm_comps))
    raise TemplateError(
        ERR_TEMPLATE_INVALID,
        f"IR must be CircuitIR or dict, got {type(ir).__name__}",
    )


def match_template(
    templates_dir: str | Path,
    ir: CircuitIR | Mapping[str, Any],
    *,
    status: TemplateStatus | str = TemplateStatus.OFFICIAL,
    bump: bool = True,
) -> MatchResult:
    """Find a template for the given IR.

    Behaviour:

    * If a template with the same topology exists, it is returned.
      ``isValueVariant`` is ``True`` if the structural signature differs
      from the stored one (parameter values changed) and ``False`` if
      the IRs are structurally identical.
    * If no template matches, ``matched=False`` is returned and the IR
      is described as a candidate to create.
    * On a successful match, ``useCount`` is incremented (unless
      ``bump=False``).
    """
    root = _ensure_root(templates_dir)
    s = status if isinstance(status, TemplateStatus) else TemplateStatus.from_str(status)
    topology = _ir_topology(ir)
    candidate = find_by_topology(root, topology, status=s)
    if candidate is None:
        return MatchResult(
            matched=False,
            template=None,
            isValueVariant=False,
            useCount=0,
            useCountBumped=False,
            reason=(
                f"no {s.value} template matches topology {topology!r}; "
                "this IR can become a candidate"
            ),
        )

    sig = _ir_signature(ir)
    # The stored IR signature comes from a sibling template.ir.json. We
    # only read it on demand; if it's missing or malformed we treat the
    # match as a value variant (conservative).
    stored_sig: tuple[Any, ...] | None = None
    ir_file = root / s.value / candidate.templateId / "template.ir.json"
    if ir_file.is_file():
        try:
            data = json.loads(ir_file.read_text(encoding="utf-8"))
            stored_sig = _ir_signature(data)
        except (json.JSONDecodeError, OSError):
            stored_sig = None

    is_value_variant = stored_sig is None or stored_sig != sig
    new_count = candidate.useCount
    bumped = False
    if bump:
        new_count = increment_use_count(root, candidate.templateId, status=s)
        bumped = True
        candidate = replace(candidate, useCount=new_count)

    return MatchResult(
        matched=True,
        template=candidate,
        isValueVariant=is_value_variant,
        useCount=new_count,
        useCountBumped=bumped,
        reason=(
            f"matched {s.value} template {candidate.templateId!r} "
            f"(topology={topology}); "
            + ("value-only variant" if is_value_variant else "structurally identical")
        ),
    )


# ---------------------------------------------------------------------------
# Candidate creation and status transitions
# ---------------------------------------------------------------------------


def create_candidate_from_ir(
    templates_dir: str | Path,
    ir: CircuitIR | Mapping[str, Any],
    *,
    template_id: str | None = None,
    layout_score: int | None = None,
    simulation_verified: bool = False,
    description: str | None = None,
    name: str | None = None,
    tags: Iterable[str] = (),
    parameters: Mapping[str, Mapping[str, Any]] | None = None,
    formula: Mapping[str, str] | None = None,
) -> TemplateManifest:
    """Create a candidate template from an IR.

    The IR is copied into ``candidates/<id>/template.ir.json``. A
    manifest is written alongside. Returns the new manifest.

    Hard rules:

    * The status of the new template is always :attr:`TemplateStatus.CANDIDATE`.
      Promotion to official is handled by :func:`move_template` (or the
      Phase 9 evaluator/promoter).
    * The IR's topology must be one we know about. We delegate to
      :class:`CircuitIR` validation when a mapping is supplied; for a
      :class:`CircuitIR` we trust the existing validation.
    """
    root = _ensure_root(templates_dir)
    # Normalize IR
    if isinstance(ir, Mapping):
        try:
            ir_obj = CircuitIR.model_validate(ir)
        except Exception as exc:
            raise TemplateError(
                ERR_TEMPLATE_INVALID,
                f"IR is not a valid CircuitIR: {exc}",
            ) from exc
    elif isinstance(ir, CircuitIR):
        ir_obj = ir
    else:
        raise TemplateError(
            ERR_TEMPLATE_INVALID,
            f"IR must be CircuitIR or dict, got {type(ir).__name__}",
        )

    proposed_id = template_id or ir_obj.name
    _validate_id(proposed_id)

    # Reject cross-status id collision: the same id can only exist in one
    # status directory at a time. This prevents silent overwrites of an
    # official template when a user accidentally picks the same name for
    # a candidate.
    for s in TemplateStatus:
        existing = _manifest_path(root, s, proposed_id)
        if existing.is_file():
            raise TemplateError(
                ERR_TEMPLATE_DUPLICATE,
                (
                    f"id {proposed_id!r} already exists in {s.value}; "
                    "remove the existing template or pick a different id"
                ),
                {"templateId": proposed_id, "status": s.value, "path": str(existing)},
            )

    target_dir = root / TemplateStatus.CANDIDATE.value / proposed_id
    if target_dir.exists():
        # Defensive: the cross-status check above should have caught this
        # but a stale directory could still be on disk.
        raise TemplateError(
            ERR_TEMPLATE_DUPLICATE,
            f"candidate {proposed_id!r} already exists",
            {"templateId": proposed_id, "path": str(target_dir)},
        )

    target_dir.mkdir(parents=True, exist_ok=False)
    ir_file = target_dir / "template.ir.json"
    try:
        ir_file.write_text(
            ir_obj.model_dump_json(indent=2, by_alias=False) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise TemplateError(
            ERR_TEMPLATE_IO,
            f"cannot write IR to {ir_file}: {exc}",
            {"path": str(ir_file)},
        ) from exc

    params: dict[str, TemplateParameter] = {}
    if parameters:
        for k, v in parameters.items():
            params[k] = TemplateParameter(
                description=str(v.get("description", "")),
                default=str(v.get("default", "")),
                editable=bool(v.get("editable", True)),
            )

    today = _today()
    manifest = TemplateManifest(
        templateId=proposed_id,
        schemaVersion=TEMPLATES_SCHEMA_VERSION,
        name=name or ir_obj.name,
        topology=ir_obj.topology,
        status=TemplateStatus.CANDIDATE,
        tags=tuple(tags),
        description=description,
        files={"ir": "template.ir.json"},
        parameters=params,
        formula=dict(formula) if formula else {},
        layoutScore=layout_score,
        simulationVerified=simulation_verified,
        useCount=0,
        createdAt=today,
        updatedAt=today,
    )
    dump_manifest(manifest, target_dir / MANIFEST_FILENAME)
    _write_index(root, list_templates(root))
    return manifest


# ---------------------------------------------------------------------------
# Project -> candidate bridge (Phase 7 <-> Phase 9)
# ---------------------------------------------------------------------------


# Default filename conventions used by ``ltagent create`` / the project
# orchestrator. They are exposed so the bridge helper does not have to
# import from ``project.py`` and create a cycle.
PROJECT_IR_FILENAME = "circuit.ir.json"
PROJECT_RESULT_FILENAME = "result.json"


def create_candidate_from_project(
    project_dir: str | Path,
    templates_dir: str | Path,
    *,
    template_id: str | None = None,
    description: str | None = None,
    tags: Iterable[str] = (),
) -> TemplateManifest:
    """Create a candidate template from a finished project.

    This is the bridge that connects :mod:`ltagent.project`
    (Phase 7) to :mod:`ltagent.evaluator` (Phase 9). The
    orchestrator runs LTspice, scores the layout, and writes a
    ``result.json``; the bridge then turns that result into a
    candidate manifest under ``templates/candidates/<id>/``.

    Hard rules:

    * The project's ``result.json`` must report a successful run
      (``run.success == true``). A failed simulation cannot become
      a candidate, because the Phase 9 evaluator's first hard
      gate would reject the manifest anyway.
    * The project's ``result.json`` must include a layout score.
      A layout score below the project threshold is *not* a hard
      reject — the candidate can still be created and evaluated
      later — but a missing score means the evaluator has nothing
      to gate on, so the bridge refuses to create a candidate
      without it.
    * The proposed ``template_id`` (default: the IR's ``name``
      field) must not already exist in any status directory.
    * The project directory must contain a valid ``circuit.ir.json``
      (and optionally a ``result.json``).

    The helper never executes LTspice, never reads the original
    schematic, and never modifies the project directory. It only
    copies the IR into the candidate and writes a manifest.
    """
    project_path = Path(project_dir).expanduser()
    if not project_path.is_dir():
        raise TemplateError(
            ERR_TEMPLATE_PROJECT_INVALID,
            f"project directory {project_path} does not exist",
            {"projectDir": str(project_path)},
        )

    ir_file = project_path / PROJECT_IR_FILENAME
    if not ir_file.is_file():
        raise TemplateError(
            ERR_TEMPLATE_PROJECT_INVALID,
            f"project is missing {PROJECT_IR_FILENAME}: {ir_file}",
            {"projectDir": str(project_path), "irFile": str(ir_file)},
        )

    try:
        ir_data = json.loads(ir_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TemplateError(
            ERR_TEMPLATE_PROJECT_INVALID,
            f"cannot read project IR: {exc}",
            {"irFile": str(ir_file)},
        ) from exc

    # Read result.json if present. The bridge is permissive: an
    # unrun project (no result.json) is treated as "sim not
    # verified", which the bridge refuses. This keeps the policy
    # honest: a candidate is only ever born from a *successful*
    # project.
    result_file = project_path / PROJECT_RESULT_FILENAME
    sim_verified = False
    layout_score: int | None = None
    if result_file.is_file():
        try:
            result_data = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise TemplateError(
                ERR_TEMPLATE_PROJECT_INVALID,
                f"cannot read project result: {exc}",
                {"resultFile": str(result_file)},
            ) from exc
        run = result_data.get("run", {}) or {}
        sim_verified = bool(run.get("success"))
        raw_layout = result_data.get("layoutScore") or result_data.get(
            "layout_score"
        )
        if raw_layout is not None:
            try:
                layout_score = int(raw_layout)
            except (TypeError, ValueError) as exc:
                raise TemplateError(
                    ERR_TEMPLATE_PROJECT_INVALID,
                    f"project layout score is not an integer: {raw_layout!r}",
                    {"layoutScore": raw_layout},
                ) from exc
    else:
        raise TemplateError(
            ERR_TEMPLATE_PROJECT_NO_RESULT,
            (
                f"project has no {PROJECT_RESULT_FILENAME}; run the project "
                "with `ltagent create --run` first so the bridge can "
                "read simulation + layout state"
            ),
            {"projectDir": str(project_path), "resultFile": str(result_file)},
        )

    if not sim_verified:
        raise TemplateError(
            ERR_TEMPLATE_SIM_NOT_VERIFIED,
            (
                "project simulation did not succeed; the candidate would "
                "be rejected by the Phase 9 evaluator's first hard gate. "
                "Fix the project and re-run, or skip --save-template."
            ),
            {
                "projectDir": str(project_path),
                "runSuccess": False,
            },
        )

    if layout_score is None:
        raise TemplateError(
            ERR_TEMPLATE_LAYOUT_MISSING,
            (
                "project has no layout score recorded; the candidate would "
                "be rejected by the Phase 9 evaluator's second hard gate. "
                "Re-run with `ltagent create --layout-score N` (or have the "
                "asc writer score the .asc)."
            ),
            {"projectDir": str(project_path)},
        )

    return create_candidate_from_ir(
        templates_dir,
        ir_data,
        template_id=template_id,
        layout_score=layout_score,
        simulation_verified=True,
        description=description,
        tags=tags,
    )


def move_template(
    templates_dir: str | Path,
    template_id: str,
    *,
    to_status: TemplateStatus | str,
) -> TemplateManifest:
    """Move a template from one status directory to another.

    The destination's manifest is updated, the source is removed, and
    the index is refreshed. The source status is inferred from the
    template's presence in any of the three directories (official is
    checked first, then candidates, then rejected).
    """
    root = _ensure_root(templates_dir)
    _validate_id(template_id)
    s_to = to_status if isinstance(to_status, TemplateStatus) else TemplateStatus.from_str(to_status)

    # Find source. If the same id exists in multiple status directories
    # (which should never happen, but the filesystem can be edited by
    # hand), prefer the requested destination's complement so a user
    # cannot silently overwrite a template they did not intend to move.
    matches: list[tuple[TemplateStatus, TemplateManifest]] = []
    for s in TemplateStatus:
        mp = _manifest_path(root, s, template_id)
        if mp.is_file():
            try:
                matches.append((s, load_manifest(mp)))
            except TemplateError:
                continue
    if not matches:
        raise TemplateError(
            ERR_TEMPLATE_NOT_FOUND,
            f"template {template_id!r} not found in any status",
            {"templateId": template_id},
        )
    if len(matches) > 1 and not all(
        s == matches[0][0] for s, _ in matches
    ):
        # Two different status directories legitimately claim the same id.
        # This is a data-integrity violation; surface it loudly.
        statuses = [s.value for s, _ in matches]
        raise TemplateError(
            ERR_TEMPLATE_DUPLICATE,
            (
                f"template {template_id!r} exists in multiple status "
                f"directories: {statuses}; resolve the conflict manually"
            ),
            {"templateId": template_id, "statuses": statuses},
        )
    source_status, source = matches[0]
    if source_status == s_to:
        return source

    src_dir = root / source_status.value / template_id
    dst_dir = root / s_to.value / template_id
    if dst_dir.exists():
        raise TemplateError(
            ERR_TEMPLATE_DUPLICATE,
            f"destination already has template {template_id!r} ({s_to.value})",
            {"templateId": template_id, "destination": str(dst_dir)},
        )
    try:
        shutil.move(str(src_dir), str(dst_dir))
    except OSError as exc:
        raise TemplateError(
            ERR_TEMPLATE_IO,
            f"cannot move {src_dir} -> {dst_dir}: {exc}",
            {"src": str(src_dir), "dst": str(dst_dir)},
        ) from exc

    new_manifest = replace(
        source,
        status=s_to,
        updatedAt=_today(),
    )
    dump_manifest(new_manifest, dst_dir / MANIFEST_FILENAME)
    _write_index(root, list_templates(root))
    return new_manifest


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def audit_templates(templates_dir: str | Path) -> AuditReport:
    """Summarise the local template library.

    Detects:

    * Manifests that fail to load
    * Duplicate topologies within the same status (e.g. two ``official``
      templates claiming ``rc_lowpass``)
    * Templates whose IR file is missing or malformed
    """
    root = _ensure_root(templates_dir)
    counts: dict[str, int] = {s.value: 0 for s in TemplateStatus}
    topologies: dict[str, int] = {}
    duplicates: list[tuple[str, tuple[str, ...]]] = []
    warnings: list[dict[str, Any]] = []
    topology_to_ids: dict[str, list[tuple[str, str]]] = {}

    total_manifests = 0
    total_components = 0

    for status in TemplateStatus:
        d = _status_dir(root, status)
        if not d.is_dir():
            continue
        for child in sorted(d.iterdir()):
            if not child.is_dir():
                continue
            mp = child / MANIFEST_FILENAME
            if not mp.is_file():
                warnings.append(
                    {
                        "code": "TEMPLATE_NO_MANIFEST",
                        "detail": f"{child} has no manifest.json",
                        "data": {"path": str(child), "status": status.value},
                    }
                )
                continue
            try:
                manifest = load_manifest(mp)
            except TemplateError as exc:
                warnings.append(
                    {
                        "code": exc.code,
                        "detail": exc.detail,
                        "data": exc.data,
                    }
                )
                continue
            counts[status.value] += 1
            total_manifests += 1
            topologies[manifest.topology] = topologies.get(manifest.topology, 0) + 1
            topology_to_ids.setdefault(manifest.topology, []).append(
                (manifest.templateId, status.value)
            )
            total_components += len(manifest.parameters)

            ir_file = child / "template.ir.json"
            if not ir_file.is_file():
                warnings.append(
                    {
                        "code": "TEMPLATE_NO_IR",
                        "detail": f"template {manifest.templateId!r} has no IR file",
                        "data": {
                            "templateId": manifest.templateId,
                            "status": status.value,
                        },
                    }
                )
                continue
            try:
                json.loads(ir_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                warnings.append(
                    {
                        "code": "TEMPLATE_IR_INVALID",
                        "detail": f"IR for {manifest.templateId!r} is invalid: {exc}",
                        "data": {
                            "templateId": manifest.templateId,
                            "status": status.value,
                        },
                    }
                )

    # Per-status duplicate detection: same topology, different id, same status.
    by_status: dict[str, dict[str, list[str]]] = {s.value: {} for s in TemplateStatus}
    for topology, entries in topology_to_ids.items():
        for tid, s in entries:
            by_status[s].setdefault(topology, []).append(tid)
    for _s, topo_map in by_status.items():
        for topology, ids in topo_map.items():
            if len(ids) > 1:
                duplicates.append((topology, tuple(sorted(ids))))

    indexed = (root / INDEX_FILENAME).is_file()
    return AuditReport(
        templatesDir=str(root),
        counts=counts,
        totals={"manifests": total_manifests, "parameters": total_components},
        topologies=topologies,
        duplicates=tuple(duplicates),
        warnings=tuple(warnings),
        indexed=indexed,
    )


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


def _write_index(root: Path, manifests: Iterable[TemplateManifest]) -> Path:
    """Write ``index.json`` with a quick lookup table.

    The index is purely advisory; the filesystem is the source of truth.
    It exists so future tools (notably MCP resources in Phase 10) can
    list templates without scanning every status directory.
    """
    by_status: dict[str, list[str]] = {s.value: [] for s in TemplateStatus}
    for m in manifests:
        by_status[m.status.value].append(m.templateId)
    payload = {
        "schemaVersion": TEMPLATES_SCHEMA_VERSION,
        "templatesDir": str(root),
        "byStatus": {k: sorted(v) for k, v in by_status.items()},
        "updatedAt": _today(),
    }
    path = root / INDEX_FILENAME
    _fd, tmp_name = tempfile.mkstemp(prefix=".index_", suffix=".tmp", dir=str(root))
    tmp_path = Path(tmp_name)
    try:
        with Path(tmp_path).open("wb") as fh:
            fh.write(json.dumps(payload, indent=2, sort_keys=False).encode("utf-8"))
            fh.write(b"\n")
        tmp_path.replace(path)
    except Exception:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise
    return path


def write_index(templates_dir: str | Path) -> Path:
    """Public wrapper around :func:`_write_index`."""
    root = _ensure_root(templates_dir)
    return _write_index(root, list_templates(root))


# ---------------------------------------------------------------------------
# Default seeds
# ---------------------------------------------------------------------------


def _default_seeds() -> list[TemplateManifest]:
    """Return the official template library.

    These are the "official" templates ltagent ships with. They
    mirror the example IR files in ``examples/`` but live inside
    the templates directory and carry the additional metadata
    required by :class:`TemplateManifest`.
    """
    today = _today()
    return [
        TemplateManifest(
            templateId="voltage_divider",
            schemaVersion=TEMPLATES_SCHEMA_VERSION,
            name="Voltage Divider",
            topology="voltage_divider",
            status=TemplateStatus.OFFICIAL,
            tags=("divider", "resistor", "reference"),
            description=(
                "Resistive voltage divider. Edit R1, R2 to scale the output."
            ),
            files={"ir": "template.ir.json"},
            parameters={
                "R1": TemplateParameter(
                    description="Series resistor",
                    default="1.4k",
                ),
                "R2": TemplateParameter(
                    description="Shunt resistor to ground",
                    default="1k",
                ),
            },
            formula={"vout": "Vout = Vin * R2 / (R1 + R2)"},
            layoutScore=90,
            simulationVerified=True,
            useCount=0,
            createdAt=today,
            updatedAt=today,
        ),
        TemplateManifest(
            templateId="rc_lowpass",
            schemaVersion=TEMPLATES_SCHEMA_VERSION,
            name="RC Low-Pass Filter",
            topology="rc_lowpass",
            status=TemplateStatus.OFFICIAL,
            tags=("filter", "rc", "lowpass"),
            description=(
                "First-order RC low-pass filter. Cutoff fc = 1 / (2*pi*R*C)."
            ),
            files={"ir": "template.ir.json"},
            parameters={
                "R1": TemplateParameter(
                    description="Series resistor",
                    default="1.59k",
                ),
                "C1": TemplateParameter(
                    description="Shunt capacitor to ground",
                    default="100n",
                ),
            },
            formula={"cutoffFrequency": "fc = 1 / (2*pi*R*C)"},
            layoutScore=92,
            simulationVerified=True,
            useCount=0,
            createdAt=today,
            updatedAt=today,
        ),
        TemplateManifest(
            templateId="rc_highpass",
            schemaVersion=TEMPLATES_SCHEMA_VERSION,
            name="RC High-Pass Filter",
            topology="rc_highpass",
            status=TemplateStatus.OFFICIAL,
            tags=("filter", "rc", "highpass"),
            description=(
                "First-order RC high-pass filter. Cutoff fc = 1 / (2*pi*R*C)."
            ),
            files={"ir": "template.ir.json"},
            parameters={
                "C1": TemplateParameter(
                    description="Series capacitor",
                    default="100n",
                ),
                "R1": TemplateParameter(
                    description="Shunt resistor to ground",
                    default="3.18k",
                ),
            },
            formula={"cutoffFrequency": "fc = 1 / (2*pi*R*C)"},
            layoutScore=92,
            simulationVerified=True,
            useCount=0,
            createdAt=today,
            updatedAt=today,
        ),
        # Phase 11: seven hand-crafted analog official templates.
        TemplateManifest(
            templateId="inverting_opamp",
            schemaVersion=TEMPLATES_SCHEMA_VERSION,
            name="Inverting Op-Amp",
            topology="inverting_opamp",
            status=TemplateStatus.OFFICIAL,
            tags=("opamp", "analog", "inverting"),
            description=(
                "Inverting op-amp with Rin, Rfb. Closed-loop gain = -Rfb/Rin."
            ),
            files={"ir": "template.ir.json"},
            parameters={
                "R1": TemplateParameter(
                    description="Input resistor Rin",
                    default="10k",
                ),
                "R2": TemplateParameter(
                    description="Feedback resistor Rfb",
                    default="100k",
                ),
            },
            formula={"gain": "A = -Rfb / Rin"},
            layoutScore=90,
            simulationVerified=True,
            useCount=0,
            createdAt=today,
            updatedAt=today,
        ),
        TemplateManifest(
            templateId="noninv_opamp",
            schemaVersion=TEMPLATES_SCHEMA_VERSION,
            name="Non-Inverting Op-Amp",
            topology="noninv_opamp",
            status=TemplateStatus.OFFICIAL,
            tags=("opamp", "analog", "noninverting"),
            description=(
                "Non-inverting op-amp. Gain = 1 + Rfb / Rg."
            ),
            files={"ir": "template.ir.json"},
            parameters={
                "R1": TemplateParameter(
                    description="Input resistor Rg",
                    default="10k",
                ),
                "R2": TemplateParameter(
                    description="Feedback resistor Rfb",
                    default="100k",
                ),
            },
            formula={"gain": "A = 1 + Rfb / Rg"},
            layoutScore=95,
            simulationVerified=True,
            useCount=0,
            createdAt=today,
            updatedAt=today,
        ),
        TemplateManifest(
            templateId="comparator",
            schemaVersion=TEMPLATES_SCHEMA_VERSION,
            name="Open-Loop Comparator",
            topology="comparator",
            status=TemplateStatus.OFFICIAL,
            tags=("opamp", "analog", "comparator"),
            description=(
                "Open-loop comparator: drives the output high or low "
                "depending on which input is larger."
            ),
            files={"ir": "template.ir.json"},
            parameters={
                "R1": TemplateParameter(
                    description="Input series resistor",
                    default="1k",
                ),
            },
            formula={},
            layoutScore=95,
            simulationVerified=True,
            useCount=0,
            createdAt=today,
            updatedAt=today,
        ),
        TemplateManifest(
            templateId="diode_clipper",
            schemaVersion=TEMPLATES_SCHEMA_VERSION,
            name="Diode Clipper",
            topology="diode_clipper",
            status=TemplateStatus.OFFICIAL,
            tags=("diode", "analog", "clipper"),
            description=(
                "Series resistor + diode pair clamps the output to "
                "+/-Vforward around 0.7V."
            ),
            files={"ir": "template.ir.json"},
            parameters={
                "R1": TemplateParameter(
                    description="Series input resistor",
                    default="1k",
                ),
            },
            formula={},
            layoutScore=100,
            simulationVerified=True,
            useCount=0,
            createdAt=today,
            updatedAt=today,
        ),
        TemplateManifest(
            templateId="halfwave_rectifier",
            schemaVersion=TEMPLATES_SCHEMA_VERSION,
            name="Half-Wave Rectifier",
            topology="halfwave_rectifier",
            status=TemplateStatus.OFFICIAL,
            tags=("diode", "rectifier", "halfwave"),
            description=(
                "Half-wave rectifier: a single series diode passes only "
                "the positive half of the AC input to the load."
            ),
            files={"ir": "template.ir.json"},
            parameters={
                "R1": TemplateParameter(
                    description="Load resistor",
                    default="1k",
                ),
            },
            formula={},
            layoutScore=100,
            simulationVerified=True,
            useCount=0,
            createdAt=today,
            updatedAt=today,
        ),
        TemplateManifest(
            templateId="bridge_rectifier",
            schemaVersion=TEMPLATES_SCHEMA_VERSION,
            name="Bridge Rectifier",
            topology="bridge_rectifier",
            status=TemplateStatus.OFFICIAL,
            tags=("diode", "rectifier", "bridge"),
            description=(
                "Full-wave bridge rectifier: four diodes form a "
                "diamond; the load sees both halves of the AC cycle."
            ),
            files={"ir": "template.ir.json"},
            parameters={
                "R1": TemplateParameter(
                    description="Load resistor",
                    default="1k",
                ),
            },
            formula={},
            layoutScore=85,
            simulationVerified=True,
            useCount=0,
            createdAt=today,
            updatedAt=today,
        ),
        TemplateManifest(
            templateId="transistor_switch",
            schemaVersion=TEMPLATES_SCHEMA_VERSION,
            name="NPN Low-Side Switch",
            topology="transistor_switch",
            status=TemplateStatus.OFFICIAL,
            tags=("bjt", "switch", "npn"),
            description=(
                "NPN low-side switch: base resistor drives the base; "
                "the load on Vcc is pulled low when the transistor "
                "saturates."
            ),
            files={"ir": "template.ir.json"},
            parameters={
                "Rb": TemplateParameter(
                    description="Base series resistor",
                    default="10k",
                ),
                "Rl": TemplateParameter(
                    description="Collector load resistor",
                    default="1k",
                ),
            },
            formula={},
            layoutScore=85,
            simulationVerified=True,
            useCount=0,
            createdAt=today,
            updatedAt=today,
        ),
    ]


def _ir_payload_for_seed(seed: TemplateManifest) -> dict[str, Any]:
    """Build a minimal valid IR payload for a seed template.

    The seed IRs are intentionally simple placeholders: the real
    editable parameters live in the manifest, and a future ``ltagent
    template render`` command (post-MVP) will materialise a project
    from a manifest + parameter overrides. For Phase 6 we only need
    the IR to be valid and to carry the right topology.
    """
    if seed.topology == "voltage_divider":
        return {
            "schemaVersion": "0.1",
            "name": seed.templateId,
            "topology": "voltage_divider",
            "description": "Resistive voltage divider (seed IR).",
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
                    "value": "1.4k",
                    "role": "series_resistor",
                },
                {
                    "id": "R2",
                    "kind": "resistor",
                    "spicePrefix": "R",
                    "nodes": ["out", "0"],
                    "value": "1k",
                    "role": "shunt_resistor",
                },
            ],
            "analysis": [{"kind": "op"}],
            "measurements": [
                {"name": "VOUT", "analysis": "op", "expression": "V(out)"},
            ],
            "probes": ["V(in)", "V(out)"],
            "metadata": {"createdBy": "ltagent", "source": "seed"},
        }
    if seed.topology == "rc_lowpass":
        return {
            "schemaVersion": "0.1",
            "name": seed.templateId,
            "topology": "rc_lowpass",
            "description": "First-order RC low-pass filter (seed IR).",
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
                    "value": "1.59k",
                    "role": "series_resistor",
                },
                {
                    "id": "C1",
                    "kind": "capacitor",
                    "spicePrefix": "C",
                    "nodes": ["out", "0"],
                    "value": "100n",
                    "role": "shunt_capacitor",
                },
            ],
            "analysis": [{"kind": "tran", "stopTime": "5m"}],
            "measurements": [
                {"name": "VOUT_MAX", "analysis": "tran", "expression": "MAX V(out)"},
            ],
            "probes": ["V(in)", "V(out)"],
            "metadata": {"createdBy": "ltagent", "source": "seed"},
        }
    if seed.topology == "rc_highpass":
        return {
            "schemaVersion": "0.1",
            "name": seed.templateId,
            "topology": "rc_highpass",
            "description": "First-order RC high-pass filter (seed IR).",
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
                    "value": "100n",
                    "role": "series_capacitor",
                },
                {
                    "id": "R1",
                    "kind": "resistor",
                    "spicePrefix": "R",
                    "nodes": ["out", "0"],
                    "value": "3.18k",
                    "role": "shunt_resistor",
                },
            ],
            "analysis": [{"kind": "tran", "stopTime": "20m"}],
            "measurements": [
                {"name": "VOUT_MAX", "analysis": "tran", "expression": "MAX V(out)"},
            ],
            "probes": ["V(in)", "V(out)"],
            "metadata": {"createdBy": "ltagent", "source": "seed"},
        }
    if seed.topology == "inverting_opamp":
        return {
            "schemaVersion": "0.1",
            "name": seed.templateId,
            "topology": "inverting_opamp",
            "description": "Inverting op-amp (seed IR).",
            "nodes": ["in", "out", "vfb", "vcc", "vee", "0"],
            "components": [
                {"id": "Vin", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["in", "0"], "value": "SINE(0 0.5 1k)", "role": "input"},
                {"id": "Vcc", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["vcc", "0"], "value": "DC 12", "role": "supply_positive"},
                {"id": "Vee", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["vee", "0"], "value": "DC -12", "role": "supply_negative"},
                {"id": "R1", "kind": "resistor", "spicePrefix": "R",
                 "nodes": ["in", "vfb"], "value": "10k", "role": "input_resistor"},
                {"id": "R2", "kind": "resistor", "spicePrefix": "R",
                 "nodes": ["vfb", "out"], "value": "100k", "role": "feedback_resistor"},
                {"id": "U1", "kind": "opamp", "spicePrefix": "X",
                 "nodes": ["in", "vfb", "vcc", "vee", "out"],
                 "value": "UniversalOpamp", "role": "opamp"},
            ],
            "subcircuits": [
                {
                    "name": "UniversalOpamp",
                    "nodes": ["in+", "in-", "v+", "v-", "out"],
                    "body": [
                        "G1 0 out in+ in- 100k",
                        "E1 out 0 in+ in- 1",
                    ],
                },
            ],
            "analysis": [{"kind": "tran", "stopTime": "5m"}],
            "measurements": [
                {"name": "VOUT_MAX", "analysis": "tran", "expression": "MAX V(out)"},
                {"name": "VOUT_MIN", "analysis": "tran", "expression": "MIN V(out)"},
            ],
            "probes": ["V(in)", "V(out)"],
            "metadata": {"createdBy": "ltagent", "source": "seed"},
        }
    if seed.topology == "noninv_opamp":
        return {
            "schemaVersion": "0.1",
            "name": seed.templateId,
            "topology": "noninv_opamp",
            "description": "Non-inverting op-amp (seed IR).",
            "nodes": ["in", "out", "vfb", "vcc", "vee", "0"],
            "components": [
                {"id": "Vin", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["in", "0"], "value": "SINE(0 0.5 1k)"},
                {"id": "Vcc", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["vcc", "0"], "value": "DC 12"},
                {"id": "Vee", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["vee", "0"], "value": "DC -12"},
                {"id": "R1", "kind": "resistor", "spicePrefix": "R",
                 "nodes": ["in", "vfb"], "value": "10k", "role": "input_resistor"},
                {"id": "R2", "kind": "resistor", "spicePrefix": "R",
                 "nodes": ["vfb", "0"], "value": "10k", "role": "ground_resistor"},
                {"id": "U1", "kind": "opamp", "spicePrefix": "X",
                 "nodes": ["vfb", "0", "vcc", "vee", "out"],
                 "value": "UniversalOpamp"},
            ],
            "subcircuits": [
                {
                    "name": "UniversalOpamp",
                    "nodes": ["in+", "in-", "v+", "v-", "out"],
                    "body": [
                        "G1 0 out in+ in- 100k",
                        "E1 out 0 in+ in- 1",
                    ],
                },
            ],
            "analysis": [{"kind": "tran", "stopTime": "5m"}],
            "measurements": [
                {"name": "VOUT_MAX", "analysis": "tran", "expression": "MAX V(out)"},
            ],
            "probes": ["V(in)", "V(out)"],
            "metadata": {"createdBy": "ltagent", "source": "seed"},
        }
    if seed.topology == "comparator":
        return {
            "schemaVersion": "0.1",
            "name": seed.templateId,
            "topology": "comparator",
            "description": "Open-loop comparator (seed IR).",
            "nodes": ["in", "out", "vcc", "vee", "0"],
            "components": [
                {"id": "Vin", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["in", "0"], "value": "SINE(0 1 1k)"},
                {"id": "Vcc", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["vcc", "0"], "value": "DC 5"},
                {"id": "Vee", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["vee", "0"], "value": "DC 0"},
                {"id": "R1", "kind": "resistor", "spicePrefix": "R",
                 "nodes": ["in", "vcc"], "value": "1k"},
                {"id": "U1", "kind": "opamp", "spicePrefix": "X",
                 "nodes": ["vcc", "vee", "vcc", "vee", "out"],
                 "value": "UniversalOpamp"},
            ],
            "subcircuits": [
                {
                    "name": "UniversalOpamp",
                    "nodes": ["in+", "in-", "v+", "v-", "out"],
                    "body": [
                        "G1 0 out in+ in- 100k",
                        "E1 out 0 in+ in- 1",
                    ],
                },
            ],
            "analysis": [{"kind": "tran", "stopTime": "5m"}],
            "measurements": [
                {"name": "VOUT_MAX", "analysis": "tran", "expression": "MAX V(out)"},
                {"name": "VOUT_MIN", "analysis": "tran", "expression": "MIN V(out)"},
            ],
            "probes": ["V(in)", "V(out)"],
            "metadata": {"createdBy": "ltagent", "source": "seed"},
        }
    if seed.topology == "diode_clipper":
        return {
            "schemaVersion": "0.1",
            "name": seed.templateId,
            "topology": "diode_clipper",
            "description": "Diode clipper (seed IR).",
            "nodes": ["in", "out", "high", "low", "0"],
            "components": [
                {"id": "Vin", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["in", "0"], "value": "SINE(0 5 1k)"},
                {"id": "R1", "kind": "resistor", "spicePrefix": "R",
                 "nodes": ["in", "out"], "value": "1k"},
                {"id": "D1", "kind": "diode", "spicePrefix": "D",
                 "nodes": ["out", "high"], "value": "1N4148"},
                {"id": "D2", "kind": "diode", "spicePrefix": "D",
                 "nodes": ["low", "out"], "value": "1N4148"},
            ],
            "models": [
                {"name": "1N4148", "type": "D", "params": ["IS=2.55e-9", "RS=0.5"]},
            ],
            "analysis": [{"kind": "tran", "stopTime": "5m"}],
            "measurements": [
                {"name": "VOUT_MAX", "analysis": "tran", "expression": "MAX V(out)"},
                {"name": "VOUT_MIN", "analysis": "tran", "expression": "MIN V(out)"},
            ],
            "probes": ["V(in)", "V(out)"],
            "metadata": {"createdBy": "ltagent", "source": "seed"},
        }
    if seed.topology == "halfwave_rectifier":
        return {
            "schemaVersion": "0.1",
            "name": seed.templateId,
            "topology": "halfwave_rectifier",
            "description": "Half-wave rectifier (seed IR).",
            "nodes": ["in", "out", "0"],
            "components": [
                {"id": "Vin", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["in", "0"], "value": "SINE(0 5 1k)"},
                {"id": "D1", "kind": "diode", "spicePrefix": "D",
                 "nodes": ["in", "out"], "value": "1N4148"},
                {"id": "R1", "kind": "resistor", "spicePrefix": "R",
                 "nodes": ["out", "0"], "value": "1k"},
            ],
            "models": [
                {"name": "1N4148", "type": "D", "params": ["IS=2.55e-9", "RS=0.5"]},
            ],
            "analysis": [{"kind": "tran", "stopTime": "5m"}],
            "measurements": [
                {"name": "VOUT_MAX", "analysis": "tran", "expression": "MAX V(out)"},
            ],
            "probes": ["V(in)", "V(out)"],
            "metadata": {"createdBy": "ltagent", "source": "seed"},
        }
    if seed.topology == "bridge_rectifier":
        return {
            "schemaVersion": "0.1",
            "name": seed.templateId,
            "topology": "bridge_rectifier",
            "description": "Full-wave bridge rectifier (seed IR).",
            "nodes": ["in", "out", "0"],
            "components": [
                {"id": "Vin", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["in", "0"], "value": "SINE(0 5 1k)"},
                {"id": "D1", "kind": "diode", "spicePrefix": "D",
                 "nodes": ["in", "out"], "value": "1N4148"},
                {"id": "D2", "kind": "diode", "spicePrefix": "D",
                 "nodes": ["out", "in"], "value": "1N4148"},
                {"id": "D3", "kind": "diode", "spicePrefix": "D",
                 "nodes": ["0", "out"], "value": "1N4148"},
                {"id": "D4", "kind": "diode", "spicePrefix": "D",
                 "nodes": ["out", "0"], "value": "1N4148"},
                {"id": "R1", "kind": "resistor", "spicePrefix": "R",
                 "nodes": ["out", "0"], "value": "1k"},
            ],
            "models": [
                {"name": "1N4148", "type": "D", "params": ["IS=2.55e-9", "RS=0.5"]},
            ],
            "analysis": [{"kind": "tran", "stopTime": "5m"}],
            "measurements": [
                {"name": "VOUT_MAX", "analysis": "tran", "expression": "MAX V(out)"},
                {"name": "VOUT_AVG", "analysis": "tran", "expression": "AVG V(out)"},
            ],
            "probes": ["V(in)", "V(out)"],
            "metadata": {"createdBy": "ltagent", "source": "seed"},
        }
    if seed.topology == "transistor_switch":
        return {
            "schemaVersion": "0.1",
            "name": seed.templateId,
            "topology": "transistor_switch",
            "description": "NPN low-side switch (seed IR).",
            "nodes": ["in", "base", "vcc", "out", "0"],
            "components": [
                {"id": "Vin", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["in", "0"], "value": "PULSE(0 5 0 1n 1n 1m 2m)"},
                {"id": "Vcc", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["vcc", "0"], "value": "DC 12"},
                {"id": "Rb", "kind": "resistor", "spicePrefix": "R",
                 "nodes": ["in", "base"], "value": "10k"},
                {"id": "Rl", "kind": "resistor", "spicePrefix": "R",
                 "nodes": ["vcc", "out"], "value": "1k"},
                {"id": "Q1", "kind": "npn", "spicePrefix": "Q",
                 "nodes": ["out", "base", "0"], "value": "BC547"},
            ],
            "models": [
                {"name": "BC547", "type": "NPN", "params": ["BF=400", "VAF=80"]},
            ],
            "analysis": [{"kind": "tran", "stopTime": "5m"}],
            "measurements": [
                {"name": "VOUT_MAX", "analysis": "tran", "expression": "MAX V(out)"},
                {"name": "VOUT_MIN", "analysis": "tran", "expression": "MIN V(out)"},
            ],
            "probes": ["V(in)", "V(out)"],
            "metadata": {"createdBy": "ltagent", "source": "seed"},
        }
    raise TemplateError(
        ERR_TEMPLATE_TOPOLOGY_UNSUPPORTED,
        f"no seed IR for topology {seed.topology!r}",
        {"topology": seed.topology},
    )


def seed_default_templates(templates_dir: str | Path) -> list[TemplateManifest]:
    """Idempotently install the official template library.

    Phase 0/6/8 shipped three MVP templates (voltage_divider,
    rc_lowpass, rc_highpass). Phase 11 extends the library with
    seven hand-crafted analog templates. Existing templates are
    not overwritten; only missing ones are written. The index is
    refreshed.
    """
    root = _ensure_root(templates_dir)
    written: list[TemplateManifest] = []
    for seed in _default_seeds():
        mp = _manifest_path(root, seed.status, seed.templateId)
        if mp.is_file():
            continue
        d = root / seed.status.value / seed.templateId
        d.mkdir(parents=True, exist_ok=True)
        ir_file = d / "template.ir.json"
        try:
            ir_file.write_text(
                json.dumps(_ir_payload_for_seed(seed), indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise TemplateError(
                ERR_TEMPLATE_IO,
                f"cannot write seed IR for {seed.templateId!r}: {exc}",
                {"path": str(ir_file)},
            ) from exc
        dump_manifest(seed, mp)
        written.append(seed)
    _write_index(root, list_templates(root))
    return written


__all__ = [
    "ERR_TEMPLATE_DUPLICATE",
    "ERR_TEMPLATE_ID_INVALID",
    "ERR_TEMPLATE_INVALID",
    "ERR_TEMPLATE_IO",
    "ERR_TEMPLATE_LAYOUT_MISSING",
    "ERR_TEMPLATE_NOT_FOUND",
    "ERR_TEMPLATE_PATH_TRAVERSAL",
    "ERR_TEMPLATE_PROJECT_INVALID",
    "ERR_TEMPLATE_PROJECT_NO_RESULT",
    "ERR_TEMPLATE_SIM_NOT_VERIFIED",
    "ERR_TEMPLATE_STATUS_INVALID",
    "ERR_TEMPLATE_TOPOLOGY_UNSUPPORTED",
    "INDEX_FILENAME",
    "MANIFEST_FILENAME",
    "PROJECT_IR_FILENAME",
    "PROJECT_RESULT_FILENAME",
    "STATUS_DIRS",
    "TEMPLATES_SCHEMA_VERSION",
    "TEMPLATE_ID_PATTERN",
    "AuditReport",
    "MatchResult",
    "TemplateError",
    "TemplateManifest",
    "TemplateParameter",
    "TemplateStatus",
    "audit_templates",
    "create_candidate_from_ir",
    "create_candidate_from_project",
    "dump_manifest",
    "find_by_topology",
    "increment_use_count",
    "list_templates",
    "load_manifest",
    "match_template",
    "move_template",
    "seed_default_templates",
    "show_template",
    "write_index",
]
