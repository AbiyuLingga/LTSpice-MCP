"""Configuration loading and validation.

Phase 0 only: read a TOML file from one of the well-known locations, fall
back to safe defaults, and validate that types are well-formed. No
required fields. No hard-coded filesystem paths in the defaults.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "config.toml"
APP_DIR_NAME = "ltagent"


@dataclass(frozen=True)
class WorkspaceConfig:
    projects_dir: str = "projects"
    templates_dir: str = "templates"


@dataclass(frozen=True)
class LTSpiceConfig:
    mode: str = "wine"
    executable: str | None = None
    wine_command: str | None = None
    working_dir: str | None = None


@dataclass(frozen=True)
class RunnerConfig:
    timeout_seconds: int = 30
    kill_on_timeout: bool = True
    save_raw: bool = False
    force_ascii_raw: bool = False
    run_in_temp_dir: bool = True


@dataclass(frozen=True)
class LayoutConfig:
    grid_x: int = 160
    grid_y: int = 96
    main_y: int = 160
    ground_y: int = 352
    min_spacing: int = 80
    official_template_min_score: int = 85


@dataclass(frozen=True)
class TemplatesConfig:
    auto_promote: bool = False
    candidate_threshold: int = 3
    official_threshold: int = 6


@dataclass(frozen=True)
class AgentConfig:
    json_output_default: bool = True
    safe_mode: bool = True


@dataclass(frozen=True)
class Config:
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    ltspice: LTSpiceConfig = field(default_factory=LTSpiceConfig)
    runner: RunnerConfig = field(default_factory=RunnerConfig)
    layout: LayoutConfig = field(default_factory=LayoutConfig)
    templates: TemplatesConfig = field(default_factory=TemplatesConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    source_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "workspace": asdict(self.workspace),
            "ltspice": asdict(self.ltspice),
            "runner": asdict(self.runner),
            "layout": asdict(self.layout),
            "templates": asdict(self.templates),
            "agent": asdict(self.agent),
        }
        if self.source_path is not None:
            d["source_path"] = str(self.source_path)
        return d


def default_config() -> Config:
    """Return a Config populated with built-in defaults."""
    return Config()


def _config_search_paths() -> list[Path]:
    """Return the candidate config file paths, highest priority first."""
    cwd = Path.cwd()
    paths: list[Path] = [cwd / CONFIG_FILENAME]

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        paths.append(Path(xdg) / APP_DIR_NAME / CONFIG_FILENAME)
    else:
        home = Path.home()
        paths.append(home / ".config" / APP_DIR_NAME / CONFIG_FILENAME)

    return paths


def find_config_file() -> Path | None:
    """Return the first config file that exists, or None."""
    for p in _config_search_paths():
        if p.is_file():
            return p
    return None


class ConfigError(ValueError):
    """Raised when a config file is present but invalid."""


def _coerce_section(raw: dict[str, Any], klass: type, errors: list[str], section: str) -> Any:
    """Build a frozen dataclass from a raw dict, collecting type errors."""
    if not isinstance(raw, dict):
        errors.append(f"{section}: expected a table, got {type(raw).__name__}")
        return klass()
    valid_fields = {f for f in klass.__dataclass_fields__}  # type: ignore[attr-defined]
    unknown = set(raw) - valid_fields
    if unknown:
        errors.append(f"{section}: unknown keys {sorted(unknown)}")
    kwargs: dict[str, Any] = {}
    for f in valid_fields:
        if f not in raw:
            continue
        value = raw[f]
        ftype = klass.__dataclass_fields__[f].type  # type: ignore[attr-defined]
        if ftype == "str" and not isinstance(value, str):
            errors.append(f"{section}.{f}: expected str, got {type(value).__name__}")
            continue
        if ftype == "str | None" and value is not None and not isinstance(value, str):
            errors.append(f"{section}.{f}: expected str|null, got {type(value).__name__}")
            continue
        if ftype == "int" and (not isinstance(value, int) or isinstance(value, bool)):
            errors.append(f"{section}.{f}: expected int, got {type(value).__name__}")
            continue
        if ftype == "bool" and not isinstance(value, bool):
            errors.append(f"{section}.{f}: expected bool, got {type(value).__name__}")
            continue
        kwargs[f] = value
    try:
        return klass(**kwargs)
    except TypeError as exc:
        errors.append(f"{section}: {exc}")
        return klass()


def load_config(path: Path | None = None) -> Config:
    """Load configuration from a path (or the first found in the search list).

    Returns defaults if no file is found. Raises ``ConfigError`` if the file
    is present but invalid.
    """
    if path is None:
        path = find_config_file()
    if path is None:
        return default_config()
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except FileNotFoundError:
        return default_config()
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"failed to parse {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"failed to read {path}: {exc}") from exc

    errors: list[str] = []
    workspace = _coerce_section(raw.get("workspace", {}), WorkspaceConfig, errors, "workspace")
    ltspice = _coerce_section(raw.get("ltspice", {}), LTSpiceConfig, errors, "ltspice")
    runner = _coerce_section(raw.get("runner", {}), RunnerConfig, errors, "runner")
    layout = _coerce_section(raw.get("layout", {}), LayoutConfig, errors, "layout")
    templates = _coerce_section(raw.get("templates", {}), TemplatesConfig, errors, "templates")
    agent = _coerce_section(raw.get("agent", {}), AgentConfig, errors, "agent")

    cfg = Config(
        workspace=workspace,
        ltspice=ltspice,
        runner=runner,
        layout=layout,
        templates=templates,
        agent=agent,
        source_path=path,
    )

    if errors:
        raise ConfigError("; ".join(errors))

    if cfg.ltspice.mode not in ("wine", "native"):
        raise ConfigError(f"ltspice.mode: expected 'wine' or 'native', got {cfg.ltspice.mode!r}")

    return cfg


def merge_overlay(base: Config, overlay: Config) -> Config:
    """Return a new Config with overlay's non-source fields preferred when set.

    Used by tests and (later) by ``ltagent init`` to layer project-local
    settings on top of user-level defaults.
    """
    if base.source_path is None and overlay.source_path is not None:
        return replace(overlay, source_path=overlay.source_path)
    return base


def search_paths_report() -> list[str]:
    """Return a list of strings describing the search order. Used by ``config show``."""
    return [str(p) for p in _config_search_paths()]


if __name__ == "__main__":  # pragma: no cover - manual debug only
    try:
        print(load_config().to_dict())
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        sys.exit(2)
