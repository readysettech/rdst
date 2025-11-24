# RDST - ReadySet Diagnostics & SQL Tuning

A command-line interface for diagnostics, query analysis, performance tuning, and caching with ReadySet.

## Installation

RDST is distributed as a Python package and can be installed using `uvx` or `pipx`.

### Using uvx (recommended)

Download and run RDST directly without installation:

```bash
# Download from S3 (production)
aws s3 cp s3://readyset-rdst/stage01/latest/ . --recursive --exclude "*" --include "*.whl"
uvx ./rdst-*.whl --help

# Or download from S3 (dev)
aws s3 cp s3://readyset-rdst-test/dev01/latest/ . --recursive --exclude "*" --include "*.whl"
uvx ./rdst-*.whl --help
```

### Using pipx

Download and install globally:

```bash
# Download from S3 (production)
aws s3 cp s3://readyset-rdst/stage01/latest/ . --recursive --exclude "*" --include "*.whl"
pipx install ./rdst-*.whl

# Or download from S3 (dev)
aws s3 cp s3://readyset-rdst-test/dev01/latest/ . --recursive --exclude "*" --include "*.whl"
pipx install ./rdst-*.whl

# Run installed version
rdst --help

# Upgrade to latest version
aws s3 cp s3://readyset-rdst/stage01/latest/ . --recursive --exclude "*" --include "*.whl"
pipx upgrade rdst --pip-args="--force-reinstall" ./rdst-*.whl
```

### Alternative: HTTPS URLs

If the S3 bucket is public, you can install directly via HTTPS:

```bash
# Using uvx (runs directly, no installation)
uvx https://readyset-rdst.s3.amazonaws.com/stage01/latest/rdst-0.1.0-py3-none-any.whl --help
uvx https://readyset-rdst.s3.amazonaws.com/stage01/latest/rdst-0.1.0-py3-none-any.whl analyze "SELECT * FROM users"

# Using pipx (install globally)
pipx install https://readyset-rdst.s3.amazonaws.com/stage01/latest/rdst-0.1.0-py3-none-any.whl

# Upgrade with pipx
pipx upgrade rdst --pip-args="--force-reinstall" \
  https://readyset-rdst.s3.amazonaws.com/stage01/latest/rdst-0.1.0-py3-none-any.whl
```

## Build from Source

RDST uses Python 3.11+ and modern Python packaging.

```bash
# Install dependencies (using uv)
uv pip install -r requirements.txt

# Run RDST
python rdst.py --help

# Or build and install locally
python -m build
pipx install dist/rdst-0.1.0-py3-none-any.whl
```

## Building Distribution Packages

RDST uses Python's standard build system. The Buildkite pipeline automatically builds and uploads packages to S3 on merge to main.

## Commands

- `rdst configure` - Manage database targets and connection profiles
- `rdst top` - Live view of top slow queries
- `rdst analyze` - Analyze and explain SQL queries
- `rdst tune` - Get optimization suggestions for queries
- `rdst cache` - Evaluate ReadySet caching benefits
- `rdst init` - First-time setup wizard
- `rdst query` - Manage query registry
- `rdst version` - Show version information

## Development

RDST is part of the ReadySet monorepo. This directory contains:
- `rdst.py` - Main CLI entry point
- `lib/` - Core functionality modules
- `pyproject.toml` - Python package configuration
- `.buildkite/` - CI/CD pipeline for building and deploying packages
