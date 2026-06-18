"""Allow ``python -m ltagent ...`` to behave like the ``ltagent`` script."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
