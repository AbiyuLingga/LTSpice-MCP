"""Codex CLI integration (Phase 9).

Installs, inspects, and removes the ``ltagent-mcp`` entry in the
local Codex configuration so a Codex client picks up the curated
workbench v2 tools.

The Codex config is a TOML file located at one of (in order):

* ``$LTAGENT_CODEX_CONFIG`` (explicit override)
* ``$XDG_CONFIG_HOME/codex/config.toml``
* ``~/.config/codex/config.toml``

The file may or may not exist; :func:`cmd_codex_install` creates
the parent directory and merges a single ``[mcp_servers.ltagent]``
section. Existing entries are preserved.

The functions in this module are pure-Python and stdlib-only.
They never run subprocesses. They never touch the network.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CODEX_PROJECT_NAME = "ltagent"
CODEX_COMMAND = "ltagent-mcp"
CODEX_SECTION = "mcp_servers"
CODEX_SDK_ERROR_HINT = (
    "pip install \"ltspice-ai-agent[mcp]\" "
    "(or run `uv add ltspice-ai-agent[mcp]`)"
)

ENV_CODEX_CONFIG = "LTAGENT_CODEX_CONFIG"


def get_default_codex_config_path() -> Path:
    """Return the platform-appropriate default Codex config path.

    The path is not guaranteed to exist.
    """
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "codex" / "config.toml"
        return Path.home() / "AppData" / "Roaming" / "codex" / "config.toml"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "codex" / "config.toml"
    return Path.home() / ".config" / "codex" / "config.toml"


def resolve_codex_config_path(explicit: str | None = None) -> Path:
    """Resolve the Codex config path, allowing explicit override.

    Resolution order: explicit > ``LTAGENT_CODEX_CONFIG`` env >
    :func:`get_default_codex_config_path`.
    """
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get(ENV_CODEX_CONFIG)
    if env:
        return Path(env).expanduser()
    return get_default_codex_config_path().expanduser()


@dataclass(frozen=True)
class CodexInstallResult:
    path: Path
    created: bool
    dryRun: bool
    server: dict[str, Any] = field(default_factory=dict)


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _coerce_toml_text(payload: Any, prefix: str = "") -> str:
    """Render a Python structure back to TOML with stable ordering.

    The Codex config is small. A purpose-built emitter keeps the
    file diff-friendly and avoids pulling in another dependency.
    Nested tables use dotted ``[a.b]`` headers, which the
    :mod:`tomllib` parser understands.
    """

    def _render_scalar(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            return repr(value)
        escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    def _render_array(value: list[Any]) -> str:
        if not value:
            return "[]"
        return "[" + ", ".join(_render_scalar(v) for v in value) + "]"

    if not isinstance(payload, dict):
        return _render_scalar(payload)

    lines: list[str] = []
    sub_tables: list[tuple[str, dict[str, Any]]] = []
    for key in sorted(payload.keys()):
        value = payload[key]
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            sub_tables.append((full_key, value))
        elif isinstance(value, list):
            lines.append(f"{key} = {_render_array(value)}")
        else:
            lines.append(f"{key} = {_render_scalar(value)}")
    parts: list[str] = []
    if lines and prefix:
        parts.append(f"[{prefix}]")
    parts.extend(lines)
    for full_key, sub in sub_tables:
        parts.append("")
        parts.append(_coerce_toml_text(sub, prefix=full_key))
    return "\n".join(p for p in parts if p != "" or len(parts) == 1)


def codex_install(
    *,
    config_path: Path | None = None,
    command: str = CODEX_COMMAND,
    dry_run: bool = False,
) -> CodexInstallResult:
    """Write the ``[mcp_servers.ltagent]`` section into the Codex config.

    The function never deletes unrelated keys. If the file does not
    exist yet it is created with a header comment; existing
    ``[mcp_servers.*]`` sections are preserved.
    """
    target = resolve_codex_config_path(
        str(config_path) if config_path is not None else None
    )
    payload = _read_toml(target)
    server_section: dict[str, Any] = {
        "command": command,
        "args": [],
    }
    servers = payload.get(CODEX_SECTION, {})
    if not isinstance(servers, dict):
        servers = {}
    servers[CODEX_PROJECT_NAME] = server_section
    payload[CODEX_SECTION] = servers
    header = "# Managed by ltagent codex install; do not edit by hand.\n"
    body = _coerce_toml_text(payload)
    created = not target.is_file()
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(header + body + "\n", encoding="utf-8")
    return CodexInstallResult(
        path=target,
        created=created,
        dryRun=dry_run,
        server=server_section,
    )


def codex_uninstall(
    *,
    config_path: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Remove the ``[mcp_servers.ltagent]`` section from the Codex config."""
    target = resolve_codex_config_path(
        str(config_path) if config_path is not None else None
    )
    payload = _read_toml(target)
    servers = payload.get(CODEX_SECTION, {})
    removed = False
    if isinstance(servers, dict) and CODEX_PROJECT_NAME in servers:
        del servers[CODEX_PROJECT_NAME]
        removed = True
        if not servers:
            payload.pop(CODEX_SECTION, None)
    else:
        payload.pop(CODEX_SECTION, None)
    existed = target.is_file()
    if not dry_run and (existed or removed):
        if payload:
            header = "# Managed by ltagent codex uninstall; do not edit by hand.\n"
            target.write_text(header + _coerce_toml_text(payload) + "\n", encoding="utf-8")
        elif existed:
            target.unlink()
    return {
        "path": str(target),
        "existed": existed,
        "removed": removed,
        "dryRun": dry_run,
    }


def codex_doctor(
    *,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Inspect the Codex config and report on the ltagent server entry."""
    target = resolve_codex_config_path(
        str(config_path) if config_path is not None else None
    )
    payload = _read_toml(target)
    servers = payload.get(CODEX_SECTION, {})
    if not isinstance(servers, dict):
        servers = {}
    entry = servers.get(CODEX_PROJECT_NAME) if isinstance(servers, dict) else None
    issues: list[dict[str, Any]] = []
    if entry is None:
        issues.append(
            {
                "code": "CODEX_SERVER_NOT_INSTALLED",
                "detail": "ltagent is not registered in the Codex config",
            }
        )
    else:
        command = entry.get("command")
        if command != CODEX_COMMAND:
            issues.append(
                {
                    "code": "CODEX_COMMAND_MISMATCH",
                    "detail": f"expected command '{CODEX_COMMAND}', got '{command}'",
                }
            )
    sdk_status = _probe_mcp_sdk()
    return {
        "path": str(target),
        "exists": target.is_file(),
        "server": entry if isinstance(entry, dict) else None,
        "issues": issues,
        "mcpSdk": sdk_status,
    }


def _probe_mcp_sdk() -> dict[str, Any]:
    """Return whether the optional MCP SDK is importable."""
    try:
        import mcp.server.fastmcp  # noqa: F401
    except Exception as exc:  # pragma: no cover - exercised by env gap
        return {
            "available": False,
            "error": repr(exc),
            "installHint": CODEX_SDK_ERROR_HINT,
        }
    return {"available": True}


__all__ = [
    "CODEX_COMMAND",
    "CODEX_PROJECT_NAME",
    "CODEX_SECTION",
    "CodexInstallResult",
    "codex_doctor",
    "codex_install",
    "codex_uninstall",
    "get_default_codex_config_path",
    "resolve_codex_config_path",
]
