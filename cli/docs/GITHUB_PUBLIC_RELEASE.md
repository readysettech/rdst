# RDST Public GitHub Release

This document explains what is published to the public GitHub repository at [github.com/readysettech/rdst](https://github.com/readysettech/rdst) and how the release pipeline works.

## Overview

RDST (Readyset Data and SQL Toolkit) is a CLI tool for database diagnostics,
query analysis, and caching optimization with Readyset. The authoritative
license is the `LICENSE` file at the repo root; `pyproject.toml` must agree
with it, since that is what PyPI shows.

**Public Repository**: https://github.com/readysettech/rdst

## What Gets Published

### Included in the Public Repository

The public repository is the whole RDST suite, not the CLI alone: the Python
tool, the web UI it serves, the desktop shell, and the design system they share.
Each lands under its own top-level directory.

| Published path | Comes from |
|------|-------------|
| `cli/` | `rdst/` — CLI entry point, `mcp_server.py`, `features/`, `shared/`, `tests/`, `devtools/`, `pyproject.toml`, `docs/` |
| `web/` | `web-apps/apps/rdst/` — the browser UI |
| `desktop/` | `web-apps/apps/rdst-desktop/` — the Electron shell |
| `packages/` | `web-apps/packages/{ui-new,ui-icons,tailwind-base,typescript-config}/` |
| repo root | `web-apps/tools/public-mirror/root/` — a generated pnpm+turbo workspace, plus `README.md`, `CONTRIBUTING.md`, `LICENSE` and `LICENSE-THIRD-PARTY` |

Because `pyproject.toml` is a level down, installing straight from git needs
`pip install "git+https://github.com/readysettech/rdst.git#subdirectory=cli"`.
PyPI and the installer script are unaffected.

The root workspace files are **generated** by
`web-apps/tools/public-mirror/scripts/build-public-root.mjs` and checked in
under that overlay. A drift gate regenerates them, fails on a difference, and
then installs and builds the filtered tree, so they cannot rot unnoticed —
but equally, editing them by hand does not survive.

### Excluded from the Public Repository

Exclusion is done by the josh filter, **not** by `.gitignore`. josh rewrites the
monorepo's history through that filter, so a path is only kept out of the public
repo if the filter excludes it. Files that are tracked in the monorepo reach
GitHub even when `.gitignore` lists them.

The filter is `web-apps/tools/public-mirror/filter.josh`, which is also what the
pre- and post-merge sanity checks run to derive the publish set — one
definition, so the gates and the push cannot disagree. Read it there rather than
here; it carries a comment for every exclusion. In outline:

| Excluded | Reason |
|------|--------|
| `rdst/keyservice/` | Separate service, not part of the CLI |
| `rdst/.buildkite/` | Internal CI/CD pipelines |
| `rdst/features/qpdemo/assets/baked/build.sh` | Hardcodes an internal AWS account |
| `apps/rdst/public/{fonts,icons}`, `apps/rdst/src/fonts.css`, `packages/ui-icons/svg` | Commercially licensed type and artwork; the mirror overlay supplies open replacements at the same published paths |
| `apps/rdst/scripts/run-*-ci.sh` | Internal CI entry points; they source `rdst/.buildkite/`, which is excluded |
| `apps/rdst/pnpm-lock.yaml` | Stale nested lockfile; the workspace root owns the only one a checkout should see |

Anything else tracked under those trees is published, including its full
history. Untracked paths (`venv/`, `__pycache__/`, `build/`, `dist/`, `.rdst/`,
`node_modules/`) never enter git, so they never reach GitHub -- but that is a
property of never being committed, not of `.gitignore` filtering the push.

Note that `docs/internal/` and `tests/integration/run_tests_local.sh` appear in
`rdst/.gitignore` but are **not** excluded by the filter. They stay private
because nobody has committed them, and `DENYPATH` entries assert they are absent
from the publish set. `tests/integration/README.md` *is* tracked, and therefore
*is* published, despite being listed in `.gitignore`.

## Security Checks

`check_github_push.sh` runs pre-merge and again post-merge, with the patterns
in `.buildkite/github_push_patterns.conf`. It derives the set of files to scan
by **running the josh filter**, so it reads exactly what would be pushed rather
than a second, hand-written model of the filter that could drift from it. That
is why it runs inside the josh container, and why its paths are public ones
(`cli/…`, `web/…`).

Pre-merge is the one that matters: a developer who adds a licensed font weight
or an internal hostname finds out at code review, not on release night.

### Forbidden Patterns (Block Push)

These patterns will **fail the build** if found in any file being pushed:

- AWS account IDs and S3 buckets
- DuploCloud infrastructure references
- Internal Supabase secrets
- Internal tenant names
- Anthropic API keys, matched by key shape rather than by the `sk-ant-` prefix,
  which appears legitimately in help text and validation
- Commercially licensed font and icon names

See `.buildkite/github_push_patterns.conf` for the full list.

### Warning Patterns (Log Warning)

These patterns generate warnings but don't block:

- Internal API URLs
- Duplo environment variables
- Public hosts that are legitimately named but worth a second look: the
  downloads host, the keyservice worker, the CLI's PostHog project key

### Licensed Asset Bytes

Separately from the patterns, every published file is compared by **content**
against the licensed private font and icon trees. A pattern catches a licensed
asset arriving under a name we thought of; this catches one arriving under a
name we did not — a licensed face renamed to `Outfit-Bold.woff2`, or vendor
artwork copied into the overlay. Four files are legitimately identical in both
trees (IBM Plex Mono, the generated `svg/README.md`, and `querypilot.svg`) and
are listed in the script.

### Denied Paths

`DENYPATH` entries name published paths the filter must not emit, and fail the
build if it does. Unlike the `.gitignore` assertion below, they say nothing
about whether a path is ignored: the licensed fonts and artwork are
legitimately tracked in the monorepo and must stay tracked.

### Gitignore Verification

The gate also runs a `.gitignore` assertion. Note that this is hygiene only:
`.gitignore` has no bearing on what josh publishes, so a pass here is not
evidence that a path stays private. The josh filter is the only control
that does that. The paths asserted:

- `.buildkite/`
- `docs/internal/`
- `tests/integration/run_tests_local.sh`
- `tests/integration/README.md`

## Code Structure

Behaviour lives under `features/`, one directory per feature, with
cross-cutting code in `shared/`. See ARCHITECTURE.md.

```
features/<name>/
  cli/command.py     # CLI surface
  api/routes.py      # HTTP surface
  service.py         # behaviour shared by both
shared/
  cli/  ui/  config/  llm_manager/  api/  deploy/  query_registry/
```

## Package Distribution

RDST is published to PyPI as `rdst` and distributed through the first-party
installer:

```bash
curl -fsSL https://downloads.readyset.io/packages/rdst-cli/install.sh | sh
```

The mutable installer is pinned to the exact PyPI release validated on native
macOS, Linux x86_64, and Linux arm64 agents before publication. It installs into
a private, user-owned runtime. Existing uv and pipx users can continue to
install from PyPI directly.

## For Maintainers

### Adding New Files

When adding new files, consider:

1. **Does it contain internal infrastructure references?** → Exclude it in
   `filter.josh` (`.gitignore` will not keep it private)
2. **Does it contain secrets or internal URLs?** → Add patterns to `github_push_patterns.conf`
3. **Is it a licensed asset?** → Exclude it, and add an open replacement to the
   mirror overlay under the same published path
4. **Is it user-facing?** → Include in public repo

### Updating the Pipeline

- `web-apps/tools/public-mirror/filter.josh` - what is published, and where
- `.buildkite/check_github_push.sh` - runs the security and licence checks
- `.buildkite/github_push_patterns.conf` - forbidden/warning patterns, denied paths
- `.buildkite/pipeline.josh.yml` - the push itself
- `.buildkite/detect_pipeline_areas.sh` - where the checks and the approval sit

### Manual Verification

Both need `josh-filter`: on `PATH`, at `$JOSH_FILTER`, or in a container image
named by `$JOSH_IMAGE` (build one from `josh/Dockerfile`).

```bash
# What the deny-list sees, which is exactly what would be pushed
./.buildkite/check_github_push.sh --strict

# The publish set as a file listing
../web-apps/tools/public-mirror/scripts/materialize-public-tree.sh --list

# The public tree on disk, to read or to build
../web-apps/tools/public-mirror/scripts/materialize-public-tree.sh --out /tmp/rdst-public
```

## License

See [LICENSE](../LICENSE). Keep `pyproject.toml`'s `license` field and its
OSI classifier in step with that file -- they are published to PyPI and are
what users actually read.

Copyright (c) 2024-2025 Readyset Technology, Inc.
