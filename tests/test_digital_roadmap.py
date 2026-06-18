"""Smoke check: the Phase 12 roadmap doc exists and is clearly
future-only (no code is gated on it; the v1 surface is unchanged).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ROADMAP_PATH = REPO_ROOT / "docs" / "digital" / "roadmap.md"
PLAN_PATH = REPO_ROOT / "docs" / "digital" / "plan-tiny8-agent.md"


def test_roadmap_doc_exists() -> None:
    assert ROADMAP_PATH.exists(), (
        f"roadmap doc not found at {ROADMAP_PATH}"
    )


def test_roadmap_doc_is_marked_future() -> None:
    text = ROADMAP_PATH.read_text(encoding="utf-8")
    assert "roadmap" in text.lower()
    # The doc must make clear nothing here is implemented.
    assert "not implemented" in text.lower() or "future" in text.lower()


def test_roadmap_doc_lists_phases() -> None:
    text = ROADMAP_PATH.read_text(encoding="utf-8")
    # The roadmap has at least 5 numbered phases.
    for marker in ("R1.", "R2.", "R3.", "R4.", "R5."):
        assert marker in text, f"missing phase marker {marker}"


def test_roadmap_doc_links_from_plan() -> None:
    text = PLAN_PATH.read_text(encoding="utf-8")
    assert "roadmap.md" in text, "plan doc should link to roadmap"
