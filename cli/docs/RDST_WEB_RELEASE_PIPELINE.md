# RDST Web Release Pipeline

## Purpose and Scope
This document describes how RDST frontend assets are released into the RDST Python package.

Scope:
- Changes under `rdst/*`
- Changes under `web-apps/apps/rdst/*`
- Shared web-app dependency files:
  - `web-apps/packages/*`
  - `web-apps/package.json`
  - `web-apps/turbo.json`
  - `web-apps/pnpm-lock.yaml`

For both scopes above, `readyset-rdst` is the release owner for RDST web assets.

## High-Level Sequence
1. Monorepo pipeline detects changed files.
2. If changes include `rdst/*`, `web-apps/apps/rdst/*`, or shared web-app dependency files, it triggers `readyset-rdst`.
3. Shared web-app dependency file changes also trigger `web-apps` pipeline.
4. In `readyset-rdst`, main branch step `build-rdst-web` builds RDST frontend from `web-apps/apps/rdst`.
5. `build-rdst-web` creates and uploads:
   - `rdst/.buildkite-artifacts/rdst-web/rdst-web-dist.tar.gz`
   - `rdst/.buildkite-artifacts/rdst-web/rdst-web-metadata.json`
6. Step `build-package` (depends on `build-rdst-web`) downloads those artifacts from the same build.
7. `build-package` validates metadata + checksum + commit and embeds assets into `rdst/lib/web_dist/`.
8. RDST package build/publish continues from the embedded frontend assets.

## Sequence Diagram
```text
monorepo -> readyset-rdst: trigger (rdst/*, web-apps/apps/rdst/*, shared web-app deps)
monorepo -> web-apps: trigger (shared web-app deps)
readyset-rdst: build-rdst-web
build-rdst-web -> artifacts: upload tar.gz + metadata.json
readyset-rdst: build-package (depends_on build-rdst-web)
build-package -> artifacts: download tar.gz + metadata.json
build-package: validate + extract to rdst/lib/web_dist/
build-package -> publish steps: wheel/sdist -> deploy
```

## Ownership Map
- Monorepo trigger routing:
  - `.buildkite/generate_monorepo_pipeline.sh`
- RDST frontend build artifact producer:
  - `rdst/.buildkite/pipeline.yml` (`build-rdst-web`)
  - `rdst/.buildkite/build_rdst_web_artifact.sh`
- RDST artifact consumer + embedder:
  - `rdst/.buildkite/build_package.sh`
  - `rdst/pyproject.toml`

`web-apps` pipeline has no RDST-web release responsibility in this model.

## Artifact Contract
Artifact files:
- `rdst/.buildkite-artifacts/rdst-web/rdst-web-dist.tar.gz`
- `rdst/.buildkite-artifacts/rdst-web/rdst-web-metadata.json`

Required metadata keys:
- `commit`
- `frontend_version`
- `bundle_sha256`
- `bundle_format` (must be `tar.gz`)
- `bundle_root` (must be `.`)
- `built_at`

## Validation Rules in `build_package.sh`
`build-package` fails hard when any of these checks fail:
1. Missing artifact files.
2. Missing required metadata keys.
3. `bundle_format` is not `tar.gz`.
4. `metadata.commit` does not match `BUILDKITE_COMMIT` (in Buildkite).
5. Tarball SHA256 does not match `metadata.bundle_sha256`.
6. Extracted frontend does not contain `rdst/lib/web_dist/index.html`.

## Embedding Into PyPI Artifact
1. Extract tarball into `rdst/lib/web_dist/`.
2. Copy metadata to `rdst/lib/web_dist/.rdst_web_metadata.json`.
3. Include `lib/web_dist` in both wheel and sdist using `rdst/pyproject.toml` force-include rules.

Runtime behavior:
- `rdst web` serves embedded `lib/web_dist` when packaged assets are present.
- If embedded assets are missing, `rdst web --ui auto` falls back to API-only mode.

## Gating and Failure Semantics
- `build-package` depends on `build-rdst-web`; if frontend build fails, package build does not start.
- Artifact presence and validation are hard gates for release packaging.
- Gerrit voting policy is managed separately and is not defined in this document.

## Troubleshooting
### `readyset-rdst` not triggered
- Confirm changed files include one of:
  - `rdst/*`
  - `web-apps/apps/rdst/*`
  - `web-apps/packages/*`
  - `web-apps/package.json`
  - `web-apps/turbo.json`
  - `web-apps/pnpm-lock.yaml`
- Check generated monorepo pipeline includes `build-rdst`.

### `build-rdst-web` fails
- Verify Node/pnpm bootstrapping and lockfile install.
- Verify `pnpm --filter rdst-web run build` succeeds.

### Artifact download or embed fails
- Confirm artifact paths exactly match step uploads.
- Check metadata schema and commit/checksum validation output.
- Confirm extracted `rdst/lib/web_dist/index.html` exists.

## Quick Verification Commands
```bash
# Confirm monorepo routing for RDST + shared dependency paths
rg -n "web-apps/apps/rdst|web-apps/packages|web-apps/package.json|web-apps/turbo.json|web-apps/pnpm-lock.yaml|rdst/\\*" .buildkite/generate_monorepo_pipeline.sh

# Confirm RDST pipeline owns frontend build + package dependency
rg -n "build-rdst-web|build-package|rdst/.buildkite-artifacts/rdst-web" rdst/.buildkite/pipeline.yml

# Confirm package embed logic and validation checks
rg -n "rdst/.buildkite-artifacts/rdst-web|bundle_sha256|index.html|.rdst_web_metadata.json" rdst/.buildkite/build_package.sh

# Confirm web-apps pipeline has no RDST-web build signal
rg -n "BUILD_RDST_WEB|rdst-web" web-apps/.buildkite
```
