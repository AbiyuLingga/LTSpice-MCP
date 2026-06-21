"""Unit tests for ``ltagent.config``.

These tests do not touch LTspice / Wine. They cover the search order,
defaults, malformed TOML, type errors, and mode validation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ltagent.config import (
    ConfigError,
    default_config,
    find_config_file,
    load_config,
    search_paths_report,
)


def test_default_config_has_safe_values() -> None:
    cfg = default_config()
    assert cfg.workspace.projects_dir == "projects"
    assert cfg.ltspice.mode == "wine"
    assert cfg.ltspice.executable is None
    assert cfg.runner.timeout_seconds == 30
    assert cfg.agent.safe_mode is True
    assert cfg.source_path is None


def test_load_returns_defaults_when_no_file(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    cfg = load_config()
    assert cfg.source_path is None
    assert cfg.workspace.projects_dir == "projects"


def test_load_reads_project_local_config(workspace_root: Path) -> None:
    (workspace_root / "config.toml").write_text(
        '[ltspice]\nmode = "native"\nexecutable = "/tmp/lt.exe"\n',
        encoding="utf-8",
    )
    cfg = load_config()
    assert cfg.source_path == workspace_root / "config.toml"
    assert cfg.ltspice.mode == "native"
    assert cfg.ltspice.executable == "/tmp/lt.exe"


def test_load_reads_user_level_config(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    xdg = workspace_root / "xdg"
    (xdg / "ltagent").mkdir(parents=True)
    (xdg / "ltagent" / "config.toml").write_text(
        "[runner]\ntimeout_seconds = 5\n", encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    cfg = load_config()
    assert cfg.runner.timeout_seconds == 5


def test_project_local_overrides_user_config(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    xdg = workspace_root / "xdg"
    (xdg / "ltagent").mkdir(parents=True)
    (xdg / "ltagent" / "config.toml").write_text(
        "[runner]\ntimeout_seconds = 5\n", encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    (workspace_root / "config.toml").write_text(
        "[runner]\ntimeout_seconds = 60\n", encoding="utf-8"
    )
    cfg = load_config()
    assert cfg.runner.timeout_seconds == 60


def test_load_rejects_malformed_toml(workspace_root: Path) -> None:
    (workspace_root / "config.toml").write_text("not = valid toml =", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config()


def test_load_rejects_invalid_mode(workspace_root: Path) -> None:
    (workspace_root / "config.toml").write_text('[ltspice]\nmode = "wsl"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config()


def test_load_rejects_type_errors_but_does_not_crash(workspace_root: Path) -> None:
    (workspace_root / "config.toml").write_text(
        '[runner]\ntimeout_seconds = "not_a_number"\n', encoding="utf-8"
    )
    with pytest.raises(ConfigError) as exc:
        load_config()
    assert "timeout_seconds" in str(exc.value)


def test_load_rejects_unknown_keys(workspace_root: Path) -> None:
    (workspace_root / "config.toml").write_text("[runner]\ntypo_key = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_config()
    assert "typo_key" in str(exc.value)


def test_load_rejects_bool_for_int_field(workspace_root: Path) -> None:
    (workspace_root / "config.toml").write_text(
        "[runner]\ntimeout_seconds = true\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError) as exc:
        load_config()
    assert "timeout_seconds" in str(exc.value)


def test_find_config_file_returns_none_when_absent(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert find_config_file() is None


def test_search_paths_report_is_a_list(workspace_root: Path) -> None:
    paths = search_paths_report()
    assert isinstance(paths, list)
    assert all(isinstance(p, str) for p in paths)
    assert any(p.endswith("config.toml") for p in paths)
