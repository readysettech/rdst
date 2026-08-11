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

| Path | Description |
|------|-------------|
| `rdst.py` | Main CLI entry point |
| `mcp_server.py` | MCP server for Claude Code integration |
| `features/` | One directory per feature: `cli/`, `api/`, `service.py` |
| `shared/` | Cross-feature code: UI, config, DB, LLM, API server |
| `tests/` | Test suite (unit tests, public integration tests) |
| `devtools/` | Development utilities |
| `README.md` | User-facing documentation |
| `LICENSE` | License text |
| `pyproject.toml` | Python package configuration |
| `requirements.txt` | Dependencies |
| `.gitignore` | Git ignore rules |
| `CLAUDE.md` | AI assistant instructions |
| `AGENTS.md` | Agent configuration |
| `docs/` | Public documentation |

### Excluded from the Public Repository

Exclusion is done by the josh filter in `.buildkite/pipeline.josh.yml`, **not**
by `.gitignore`. josh rewrites the monorepo's history through that filter, so a
path is only kept out of the public repo if the filter excludes it. Files that
are tracked in the monorepo reach GitHub even when `rdst/.gitignore` lists them.
The filter currently reads:

```
:/rdst:exclude[::keyservice/]:exclude[::.buildkite/]:exclude[::features/qpdemo/assets/baked/build.sh]
```

So the excluded set is exactly:

| Path | Reason |
|------|--------|
| `keyservice/` | Separate service, not part of the CLI |
| `.buildkite/` | Internal CI/CD pipelines |
| `features/qpdemo/assets/baked/build.sh` | Hardcodes an internal AWS account |

Anything else tracked under `rdst/` is published, including its full history.
Untracked paths (`venv/`, `__pycache__/`, `build/`, `dist/`, `.rdst/`) never
enter git, so they never reach GitHub -- but that is a property of never being
committed, not of `.gitignore` filtering the push.

Note that `docs/internal/` and `tests/integration/run_tests_local.sh` appear in
`rdst/.gitignore` but are **not** in the josh filter. They stay private only
because nobody has committed them. `tests/integration/README.md` *is* tracked,
and therefore *is* published, despite being listed in `.gitignore`.

## Security Checks

Before any push to GitHub, the pipeline runs security checks defined in `.buildkite/github_push_patterns.conf`:

### Forbidden Patterns (Block Push)

These patterns will **fail the build** if found in any file being pushed:

- AWS account IDs and S3 buckets
- DuploCloud infrastructure references
- Internal Supabase secrets
- Internal tenant names

See `.buildkite/github_push_patterns.conf` for the full list.

### Warning Patterns (Log Warning)

These patterns generate warnings but don't block:

- Internal API URLs
- Duplo environment variables

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

1. **Does it contain internal infrastructure references?** → Exclude it in the
   josh filter (`.gitignore` will not keep it private)
2. **Does it contain secrets or internal URLs?** → Add patterns to `github_push_patterns.conf`
3. **Is it user-facing?** → Include in public repo

### Updating the Pipeline

The GitHub push pipeline is configured in `.buildkite/`:

- `check_github_push.sh` - Runs the security pattern checks
- `github_push_patterns.conf` - Defines forbidden/warning patterns
- `pipeline.yml` - Buildkite pipeline definition

### Manual Verification

To manually check what would be pushed:

```bash
# Run the check script
./.buildkite/check_github_push.sh --strict

# See exactly what josh would publish, without pushing
/joshua.sh -f '<filter>' -r refs/heads/main -m <remote> -p false
```

## License

See [LICENSE](../LICENSE). Keep `pyproject.toml`'s `license` field and its
OSI classifier in step with that file -- they are published to PyPI and are
what users actually read.

Copyright (c) 2024-2025 Readyset Technology, Inc.
