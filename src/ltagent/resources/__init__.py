"""Package resource files for ``ltagent``.

This subpackage carries non-code artefacts that ship inside the wheel.
Use :func:`importlib.resources.files` (Python 3.9+) to read them at
runtime so the install path is irrelevant.

The Circuit IR JSON Schema lives here so the bundled ``ltagent ir
schema`` command works after a wheel install, not just from a source
checkout. ``tools/generate_schema.py`` keeps the two copies in sync.
"""
