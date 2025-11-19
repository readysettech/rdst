# RDST - ReadySet Diagnostics & SQL Tuning

A command-line interface for diagnostics, query analysis, performance tuning, and caching with ReadySet.

## Installation

RDST is distributed as platform-specific packages:

- **Debian/Ubuntu**: `.deb` package
- **RHEL/CentOS**: `.rpm` package
- **Amazon Linux 2023**: `.rpm.al23` package
- **macOS**: `.pkg` installer

Download the appropriate package for your platform and install using your system's package manager.

## Build from Source

RDST uses [uv](https://github.com/astral-sh/uv) for dependency management.

```bash
# Install dependencies
uv pip install -r requirements.txt

# Run RDST
python rdst.py --help
```

## Building Distribution Packages

See build scripts in this directory:
- `build_rdst.sh` - Main build script (runs in Docker container)
- `orchestrate_rdst.sh` - Orchestrates Docker builds and S3 uploads
- `Dockerfile.*` - Docker images for building on different platforms

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
- `common/` - Shared utilities (logger, AWS operations, etc.)
- Build scripts and Dockerfiles for package creation
