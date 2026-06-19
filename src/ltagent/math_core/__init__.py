"""Math core for ltspice-ai-agent.

Pure-Python calculation engine. The math core is the *only* place that
computes component values, picks preferred E-series values, and builds
the deterministic ``calculation.json`` / ``calculation.md`` reports
that the rest of the system consumes.

The calculation modules are re-exported for callers that need the
deterministic engine. Verification remains importable from its own
submodule to keep that boundary explicit, e.g.::

    from ltagent.math_core.verification_math import compare_formula_vs_simulation
"""

from __future__ import annotations

from . import calculation_report, formulas, standard_values, units
from .api import calculate, explain, supported_topologies

__all__ = [
    "__version__",
    "calculate",
    "calculation_report",
    "explain",
    "formulas",
    "standard_values",
    "supported_topologies",
    "units",
]

__version__ = "0.1.0"
