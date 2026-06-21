# Release artifacts

R9 produces local Linux x86_64 artifacts only.

## Build

```bash
uv run --no-sync python scripts/build_sidecar.py
cd apps/desktop
npm run tauri -- build --bundles deb,appimage
cd ../..
uv run --no-sync python scripts/release_manifest.py
uv run --no-sync python scripts/smoke_desktop_bundle.py
```

## Outputs

- `dist/ltspice_ai_agent-*.whl`
- `dist/ltspice_ai_agent-*.tar.gz`
- `apps/desktop/sidecar/ltagent-engine-x86_64-unknown-linux-gnu`
- `apps/desktop/sidecar/ltagent-mcp-x86_64-unknown-linux-gnu`
- `apps/desktop/src-tauri/target/release/bundle/deb/*.deb`
- `apps/desktop/src-tauri/target/release/bundle/appimage/*.AppImage`
- `dist/release/SHA256SUMS`
- `dist/release/manifest.json`
- `dist/release/sbom-python.json`
- `dist/release/sbom-npm.json`
- `dist/release/sbom-cargo.json`

The `.deb` recommends `ngspice`, `iverilog`, `verilator`, and `yosys`; the
application reports structured tool errors when those EDA backends are absent.

## External gates

- Live AI smoke requires an API key entered through Settings.
- Clean-install smoke expects Ubuntu 24.04 x86_64 with X11/Xvfb available.
- Linux packages are not signed in v0.1.0 local release mode.
