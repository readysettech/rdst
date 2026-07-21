# RDST Public GitHub Release

This document explains what is published to the public GitHub repository at [github.com/readysettech/rdst](https://github.com/readysettech/rdst) and how the release pipeline works.

## Overview

RDST (Readyset Data and SQL Toolkit) is a source-available CLI tool for database diagnostics, query analysis, and caching optimization with Readyset. It is released under the BSL 1.1 license.

**Public Repository**: https://github.com/readysettech/rdst

## What Gets Published

### Included in the Public Repository

| Path | Description |
|------|-------------|
| `rdst.py` | Main CLI entry point |
| `mcp_server.py` | MCP server for Claude Code integration |
| `lib/` | Core library modules (CLI commands, functions, services) |
| `tests/` | Test suite (unit tests, public integration tests) |
| `devtools/` | Development utilities |
| `README.md` | User-facing documentation |
| `LICENSE` | MIT License |
| `pyproject.toml` | Python package configuration |
| `requirements.txt` | Dependencies |
| `.gitignore` | Git ignore rules |
| `.github/` | GitHub-specific configs (issue templates) |
| `CLAUDE.md` | AI assistant instructions |
| `AGENTS.md` | Agent configuration |
| `docs/` | Public documentation |

### Excluded from the Public Repository

The following files/directories are excluded via `.gitignore` and not pushed to GitHub:

| Path | Reason |
|------|--------|
| `.buildkite/` | Internal CI/CD pipelines |
| `docs/internal/` | Internal documentation |
| `tests/integration/run_tests_local.sh` | Local test config with internal URLs |
| `tests/integration/README.md` | Internal integration test docs |
| `venv/`, `.venv/` | Local Python environments |
| `__pycache__/`, `*.pyc` | Python bytecode |
| `build/`, `dist/`, `*.egg-info/` | Build artifacts |
| `.rdst/` | User-specific local config |

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

The pipeline verifies these files are properly gitignored:

- `.buildkite/`
- `docs/internal/`
- `tests/integration/run_tests_local.sh`
- `tests/integration/README.md`

## Library Structure

The `lib/` directory contains the full RDST functionality:

```
lib/
├── cli/              # Command implementations
│   ├── rdst_cli.py   # Main RdstCLI class
│   ├── analyze_command.py
│   ├── top.py
│   └── ...
├── functions/        # Core business logic
│   ├── llm_analysis.py
│   ├── explain_analysis.py
│   └── ...
├── services/         # Service layer
├── llm_manager/      # LLM provider abstraction
├── engines/          # Ask3 text-to-SQL engine
├── semantic_layer/   # Schema metadata
├── ui/               # Rich terminal UI components
├── agent/            # Agent mode functionality
├── api/              # FastAPI server components
├── query_registry/   # Saved query management
└── ...
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

1. **Does it contain internal infrastructure references?** → Add to `.gitignore`
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

# Verify .gitignore is working
git status --ignored
```

## License

RDST is released under the BSL 1.1 license. See [LICENSE](../LICENSE) for details.

Copyright (c) 2024-2025 Readyset Technology, Inc.
