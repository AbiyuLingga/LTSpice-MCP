# Release notes

This document is the human-readable companion to `CHANGELOG.md`.
It tracks the public release pipeline for the Hardware Design
Workbench.

## Pipeline

The project follows a three-stage release pipeline:

1. **Alpha** — internal dogfood + invited testers. Binaries are
   signed with the **alpha** key. No marketing.
2. **Beta** — wider community preview. Binaries are signed with
   the **beta** key. The change log is published; backwards-
   incompatible changes are still allowed but discouraged.
3. **Stable** — production release. Binaries are signed with
   the **release** key. Tagged in Git, advertised in the
   changelog, and mirrored.

Each stage uses its own signing key. Keys are never committed
to the repository; they live in the maintainer's local
keystore / CI secret store.

## Signing keys

The release engineer is responsible for generating, protecting,
and rotating the three keys. The conventions are:

| Stage    | Key alias           | File convention (local)        |
|----------|---------------------|--------------------------------|
| alpha    | `ltagent-alpha`     | `~/.local/share/ltagent/keys/alpha.key` |
| beta     | `ltagent-beta`      | `~/.local/share/ltagent/keys/beta.key`  |
| release  | `ltagent-release`   | `~/.local/share/ltagent/keys/release.key` |

In CI the keys live as **repository secrets**:

* `LTAGENT_ALPHA_SIGNING_KEY` (base64-encoded)
* `LTAGENT_BETA_SIGNING_KEY`
* `LTAGENT_RELEASE_SIGNING_KEY`

A release engineer must:

1. Generate the key with `gpg --full-generate-key` (RSA, 4096
   bits, 1-year expiry) and export it to the local
   `~/.local/share/ltagent/keys/` path.
2. Upload the **public** key to `docs/keys/<alias>.pub` so
   downstream users can verify the signature.
3. Upload the **private** key to the CI secret store (base64).
4. Mark the key rotation in `CHANGELOG.md` under the
   "Security" heading of the next release.

> The keys are *never* committed in plain text. The CI
> secret store is the only place the private key bytes are
> allowed to exist outside the maintainer's local machine.

## How to cut a release

The `Makefile` (when present) or the manual commands below
cover the three steps. They are idempotent; re-running them on
the same tag is a no-op.

```bash
# 1. Tag the commit.
git tag -s v0.1.0-alpha.1 -m "ltagent 0.1.0-alpha.1"
git push origin v0.1.0-alpha.1

# 2. Build the sdist + wheel and the sidecar.
uv run --no-sync python scripts/build_sidecar.py

# 3. Sign the wheel with the alpha key.
gpg --armor --detach-sign --local-user ltagent-alpha \
    --output dist/ltspice_ai_agent-0.1.0a1-py3-none-any.whl.asc \
    dist/ltspice_ai_agent-0.1.0a1-py3-none-any.whl

# 4. Publish (alpha + beta only).
twine upload --repository testpypi dist/*
```

The stable release path also requires:

* The release key has been rotated within the last 12 months.
* All blocking issues from the previous beta are closed.
* The CHANGELOG "Security" heading lists the signing-key
  fingerprint and the rotation date.

## Versioning

* `0.y.z` — pre-1.0 development. Backwards-incompatible
  changes bump the minor.
* `1.y.z` — stable. Backwards-incompatible changes bump the
  major and ship with a migration note in `CHANGELOG.md`.
* `1.2.3a1` / `1.2.3b1` — alpha / beta pre-releases on PyPI.
