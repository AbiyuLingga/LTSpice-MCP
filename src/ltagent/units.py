"""Phase 2: SI unit normalization helpers.

Not implemented in Phase 0. See ``docs/PROJECT_PLAN.md`` section 11.
"""

from __future__ import annotations


def parse_spice_value(value: str) -> float | None:
    """Return the numeric value of a SPICE literal, or None if it cannot be parsed.

    Supports SI suffixes used by SPICE: ``T``, ``G``, ``Meg``, ``k``,
    ``m``, ``u``, ``n``, ``p``, ``f``. Case-insensitive. ``Meg`` is
    handled distinctly from ``m`` to match SPICE convention.
    """
    s = value.strip()
    if not s:
        return None
    # SPICE uses Meg for mega to avoid ambiguity with milli. Suffixes are
    # matched case-insensitively; ``meg`` is rewritten to ``Meg`` so the
    # single lookup works.
    suffixes: tuple[tuple[str, float], ...] = (
        ("T", 1e12),
        ("G", 1e9),
        ("Meg", 1e6),
        ("K", 1e3),
        ("k", 1e3),
        ("m", 1e-3),
        ("u", 1e-6),
        ("n", 1e-9),
        ("p", 1e-12),
        ("f", 1e-15),
    )
    s_lower = s.lower()
    for suffix, mult in suffixes:
        suffix_lower = suffix.lower()
        if s_lower.endswith(suffix_lower) and len(s) > len(suffix_lower):
            head = s[: -len(suffix_lower)]
            try:
                return float(head) * mult
            except ValueError:
                continue
    try:
        return float(s)
    except ValueError:
        return None
