"""
rdst CLI stubs package

This package provides a modern, extensible interface surface for a future CLI
that will control and inspect the ReadySet Cloud Agent. It purposefully exports
lightweight stubs that can be wired up to the concrete cloud agent modules.

Key goals:
- Stable function shapes for the CLI commands
- Centralized client to access agent modules
- Minimal side effects so it can be imported safely in any environment

Usage example (programmatic):

from lib.cli.rdst_cli import rdst

result = rdst.configure(config_path="/path/config.json")
print(result.ok, result.message)

Note: These are stubs intended to be integrated in subsequent iterations.
"""

from .rdst_cli import rdst, RdstCLI, RdstResult

__all__ = [
    "rdst",
    "RdstCLI",
    "RdstResult",
]
