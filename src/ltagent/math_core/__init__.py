"""Math core for ltspice-ai-agent.

Pure-Python calculation engine. The math core is the *only* place that
computes component values, picks preferred E-series values, and builds
the deterministic ``calculation.json`` / ``calculation.md`` reports
that the rest of the system consumes.

This package is shared across multiple agents. The Agent-5-owned
submodule is :mod:`ltagent.math_core.verification_math`; other agents
own the other submodules. To keep the package importable even when
only Agent 5's module is present, the package ``__init__`` does
*not* eagerly import any submodule. Consumers should import the
submodule they need directly, e.g.::

    from ltagent.math_core.verification_math import compare_formula_vs_simulation
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
