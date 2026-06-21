# Alpha release playbook

This playbook describes the cut-and-ship steps for an
**alpha** release. The same shape applies to beta and stable
releases; only the key alias and the publication target
change.

## Pre-flight

* `git status` is clean.
* `git log --oneline -10` shows the expected commits.
* `uv run --no-sync python -m pytest -q` is green.
* `uv run ruff check .` and `uv run --no-sync python -m mypy src` are clean.
* `CHANGELOG.md` has the alpha heading.
* `docs/RELEASE_NOTES.md` is current.

## Cut the tag

```bash
git tag -s v0.1.0-alpha.1 -m "ltagent 0.1.0-alpha.1"
git push origin v0.1.0-alpha.1
```

`-s` requires a GPG signing key. The key alias
`ltagent-alpha` must already be in your local GPG keyring
(see `docs/RELEASE_NOTES.md`).

## Build the artefacts

```bash
uv run --no-sync python scripts/build_sidecar.py
```

The script writes the wheel, the sdist, and the target-triple
PyInstaller sidecars into `dist/` and `apps/desktop/sidecar/`.

## Sign the wheel

```bash
gpg --armor --detach-sign \
    --local-user ltagent-alpha \
    --output dist/ltspice_ai_agent-0.1.0a1-py3-none-any.whl.asc \
    dist/ltspice_ai_agent-0.1.0a1-py3-none-any.whl
```

## Publish

```bash
twine upload --repository testpypi dist/*
```

The alpha key may not have write access to the production
PyPI. Use the test index for alpha / beta and the production
index for stable.

## Smoke test the published wheel

```bash
python -m venv /tmp/ltagent-alpha-venv
/tmp/ltagent-alpha-venv/bin/pip install \
    --index-url https://test.pypi.org/simple/ \
    ltspice-ai-agent==0.1.0a1
/tmp/ltagent-alpha-venv/bin/ltagent --version
/tmp/ltagent-alpha-venv/bin/ltagent codex install \
    --config /tmp/alpha-codex.toml --dry-run
/tmp/ltagent-alpha-venv/bin/python \
    /path/to/repo/scripts/smoke_codex.py
```

If all four commands return the expected values, the alpha
build is shippable. If not, **yank the tag** with
`git tag -d v0.1.0-alpha.1` and `git push origin :v0.1.0-alpha.1`.

## Notify the team

Send the alpha tag, the wheel SHA-256, and the GPG
fingerprint to the internal dogfood list. Include:

* The CHANGELOG entry.
* A link to `docs/RELEASE_NOTES.md`.
* The known-issue list (anything that is not in scope for the
  alpha).
