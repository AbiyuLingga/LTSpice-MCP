"""Security helpers shared by the CLI and the MCP server.

This module centralises the path-safety and identifier-validation
helpers that Phase 0-9 inlined inside ``cli.py`` and ``templates.py``.
Extracting them lets the Phase 10 MCP server reuse the **same**
guards as the CLI so a path the CLI refuses is also refused by an
MCP tool call (security.md section 2, plan section 17.5).

Design rules (plan §18 + security.md):

* ``safe_resolve_under`` is the only function that should produce a
  ``Path`` from user input. It rejects traversal, returns an absolute
  path, and yields a structured :class:`PathSafetyError` carrying the
  stable error code ``PATH_TRAVERSAL`` on rejection.
* Slug identifiers (template ids, project ids) are validated against
  ``^[a-z][a-z0-9_]{0,63}$``. Anything else is rejected with
  ``IDENTIFIER_INVALID``.
* MCP resource URIs use the ``ltagent://`` scheme. ``parse_resource_uri``
  splits the path into ``(kind, identifier)`` and validates the
  identifier before returning. Traversal segments (``..``, ``.``,
  absolute paths) are rejected with ``RESOURCE_URI_INVALID``.

This module never executes the shell. It never writes files. It only
validates and resolves paths.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Final
from urllib.parse import unquote, urlsplit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Stable slug pattern for template ids, project ids, and similar names.
#: Lower-case letter first, then lower-case letters / digits / underscores,
#: up to 64 chars (plan §10.1 + AGENTS.md "Generated project names match
#: the IR slug pattern ``^[a-z][a-z0-9_-]{0,63}$``").
SLUG_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

#: MCP resource URI scheme.
RESOURCE_SCHEME: Final[str] = "ltagent"

#: Allowed MCP resource kinds (matches plan §17.4).
RESOURCE_KIND_PROJECTS: Final[str] = "projects"
RESOURCE_KIND_TEMPLATES: Final[str] = "templates"
ALLOWED_RESOURCE_KINDS: Final[frozenset[str]] = frozenset(
    {RESOURCE_KIND_PROJECTS, RESOURCE_KIND_TEMPLATES}
)

#: Allowed sub-paths under a project (plan §17.4).
ALLOWED_PROJECT_RESOURCE_NAMES: Final[frozenset[str]] = frozenset(
    {"metadata", "result", "circuit-ir", "netlist", "log"}
)
#: Allowed sub-paths under a template (plan §17.4).
ALLOWED_TEMPLATE_RESOURCE_NAMES: Final[frozenset[str]] = frozenset({"metadata"})

# ---------------------------------------------------------------------------
# Error codes (stable, machine-readable)
# ---------------------------------------------------------------------------

ERR_PATH_TRAVERSAL: Final[str] = "PATH_TRAVERSAL"
ERR_PATH_OUTSIDE_ROOT: Final[str] = "PATH_OUTSIDE_ROOT"
ERR_PATH_NOT_FOUND: Final[str] = "PATH_NOT_FOUND"
ERR_PATH_IO: Final[str] = "PATH_IO"
ERR_IDENTIFIER_INVALID: Final[str] = "IDENTIFIER_INVALID"
ERR_RESOURCE_URI_INVALID: Final[str] = "RESOURCE_URI_INVALID"
ERR_RESOURCE_KIND_UNKNOWN: Final[str] = "RESOURCE_KIND_UNKNOWN"
ERR_RESOURCE_SUBPATH_INVALID: Final[str] = "RESOURCE_SUBPATH_INVALID"
ERR_RESOURCE_NOT_FOUND: Final[str] = "RESOURCE_NOT_FOUND"
ERR_RESOURCE_RAW_BLOCKED: Final[str] = "RESOURCE_RAW_BLOCKED"

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SecurityError(ValueError):
    """Base for all structured security rejections.

    Carries a stable ``code`` so the CLI / MCP layer can render the
    JSON output contract without re-parsing the message.
    """

    def __init__(
        self,
        code: str,
        message: str,
        data: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data: dict[str, object] = dict(data) if data else {}


class PathSafetyError(SecurityError):
    """Raised when a candidate path escapes its declared root."""


class IdentifierError(SecurityError):
    """Raised when a slug-style identifier is malformed."""


class ResourceUriError(SecurityError):
    """Raised when an MCP resource URI is malformed or unsupported."""


# ---------------------------------------------------------------------------
# Slug / identifier validation
# ---------------------------------------------------------------------------


def validate_slug(value: object, *, kind: str = "identifier") -> str:
    """Validate ``value`` against :data:`SLUG_PATTERN`.

    ``kind`` is included in the error data so callers can show
    "template id", "project id", etc. without re-coding the same
    string in every call site.
    """
    if not isinstance(value, str) or not SLUG_PATTERN.match(value):
        raise IdentifierError(
            ERR_IDENTIFIER_INVALID,
            f"{kind} {value!r} must match {SLUG_PATTERN.pattern}",
            {"kind": kind, "value": str(value)},
        )
    return value


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def is_within(child: Path, parent: Path) -> bool:
    """Return True iff ``child`` resolves under ``parent``.

    Both paths are resolved with ``strict=False`` so missing children
    do not raise. Symlinks are followed only via the child resolve;
    the parent is taken as-is.
    """
    try:
        child_resolved = child.resolve(strict=False)
        parent_resolved = parent.resolve(strict=False)
    except OSError:
        return False
    try:
        child_resolved.relative_to(parent_resolved)
    except ValueError:
        return False
    return True


def safe_resolve_under(
    candidate: Path | str,
    root: Path | str,
    *,
    must_exist: bool = False,
) -> Path:
    """Resolve ``candidate`` and require it to live under ``root``.

    On success returns the resolved absolute ``Path``. On rejection
    raises :class:`PathSafetyError` with code ``PATH_TRAVERSAL`` and a
    payload that names both endpoints so the caller can render a
    useful error.

    Parameters
    ----------
    candidate:
        The user-supplied path. May be relative or absolute, may use
        ``~``, and may include ``..`` segments (these are rejected).
    root:
        The directory ``candidate`` must resolve inside. Typically the
        configured projects_dir or templates_dir.
    must_exist:
        When True, additionally require the resolved path to exist.
        Raises ``PATH_NOT_FOUND`` otherwise.
    """
    if not isinstance(candidate, (str, Path)):
        raise PathSafetyError(
            ERR_PATH_OUTSIDE_ROOT,
            f"candidate must be a path, got {type(candidate).__name__}",
            {"candidate": str(candidate)},
        )
    if not isinstance(root, (str, Path)):
        raise PathSafetyError(
            ERR_PATH_OUTSIDE_ROOT,
            f"root must be a path, got {type(root).__name__}",
            {"root": str(root)},
        )
    try:
        root_resolved = Path(root).expanduser().resolve(strict=False)
        candidate_resolved = Path(candidate).expanduser().resolve(strict=False)
    except OSError as exc:
        raise PathSafetyError(
            ERR_PATH_IO,
            f"cannot resolve path: {exc}",
            {"candidate": str(candidate), "root": str(root)},
        ) from exc
    try:
        candidate_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise PathSafetyError(
            ERR_PATH_TRAVERSAL,
            f"{candidate_resolved} is not under {root_resolved}",
            {
                "candidate": str(candidate_resolved),
                "root": str(root_resolved),
            },
        ) from exc
    if must_exist and not candidate_resolved.exists():
        raise PathSafetyError(
            ERR_PATH_NOT_FOUND,
            f"{candidate_resolved} does not exist",
            {"path": str(candidate_resolved)},
        )
    return candidate_resolved


# ---------------------------------------------------------------------------
# MCP resource URI parsing
# ---------------------------------------------------------------------------


def parse_resource_uri(
    uri: str,
    *,
    allowed_kinds: Iterable[str] = ALLOWED_RESOURCE_KINDS,
    allowed_subpaths: dict[str, frozenset[str]] | None = None,
) -> tuple[str, str, str | None]:
    """Parse an ``ltagent://`` resource URI.

    Returns a 3-tuple ``(kind, identifier, subpath)``:

    * ``kind`` is one of :data:`ALLOWED_RESOURCE_KINDS` (default).
    * ``identifier`` is the slug-validated resource id (e.g. project id).
    * ``subpath`` is the optional resource sub-name (e.g. ``"result"``)
      or ``None`` for the collection root.

    The identifier is validated against :data:`SLUG_PATTERN`. The
    subpath is validated against ``allowed_subpaths[kind]`` when
    provided. Traversal segments (``..``, ``.``, absolute paths) are
    rejected. Empty identifiers are rejected.

    Raises :class:`ResourceUriError` with one of the stable codes
    defined above.
    """
    if not isinstance(uri, str) or not uri:
        raise ResourceUriError(
            ERR_RESOURCE_URI_INVALID,
            "resource uri must be a non-empty string",
            {"uri": str(uri)},
        )

    try:
        parts = urlsplit(uri)
    except ValueError as exc:
        raise ResourceUriError(
            ERR_RESOURCE_URI_INVALID,
            f"cannot parse resource uri: {exc}",
            {"uri": uri},
        ) from exc

    if parts.scheme != RESOURCE_SCHEME:
        raise ResourceUriError(
            ERR_RESOURCE_URI_INVALID,
            f"unsupported scheme {parts.scheme!r}; expected {RESOURCE_SCHEME!r}",
            {"uri": uri, "scheme": parts.scheme},
        )

    # ``/projects/abc123/result`` -> ["projects", "abc123", "result"]
    segments = [unquote(seg) for seg in parts.path.split("/") if seg]
    if not segments:
        raise ResourceUriError(
            ERR_RESOURCE_URI_INVALID,
            "resource uri is missing a kind segment",
            {"uri": uri},
        )

    kind = segments[0]
    if kind not in allowed_kinds:
        raise ResourceUriError(
            ERR_RESOURCE_KIND_UNKNOWN,
            f"resource kind {kind!r} is not supported",
            {"uri": uri, "kind": kind, "allowed": sorted(allowed_kinds)},
        )

    if len(segments) == 1:
        # collection root, e.g. ltagent://projects
        identifier = ""
        subpath: str | None = None
    elif len(segments) == 2:
        identifier = segments[1]
        subpath = None
    else:
        # last segment is the subpath; segments[1..-2] joined with '-' as id
        identifier = "-".join(segments[1:-1])
        subpath = segments[-1]

    if identifier:
        validate_slug(identifier, kind=f"{kind} id")

    if subpath is not None and allowed_subpaths is not None:
        allowed_for_kind = allowed_subpaths.get(kind)
        if allowed_for_kind is not None and subpath not in allowed_for_kind:
            raise ResourceUriError(
                ERR_RESOURCE_SUBPATH_INVALID,
                f"resource subpath {subpath!r} is not allowed for kind {kind!r}",
                {
                    "uri": uri,
                    "kind": kind,
                    "subpath": subpath,
                    "allowed": sorted(allowed_for_kind),
                },
            )

    return kind, identifier, subpath


def assert_no_raw_path(path: Path) -> Path:
    """Reject ``*.raw`` paths (plan §17.4 + section 25 risk table)."""
    if path.suffix.lower() == ".raw":
        raise PathSafetyError(
            ERR_RESOURCE_RAW_BLOCKED,
            f"{path} is a raw waveform; raw files are not exposed by MCP",
            {"path": str(path)},
        )
    return path
