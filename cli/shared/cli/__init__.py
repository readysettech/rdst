"""Shared CLI support types, parser definitions, and command helpers."""

from .types import RdstResult
from .rdst_cli import RdstCLI, rdst
from .help_command import HelpCommand
from .parser_data import (
    ArgDef,
    CommandDef,
    MutuallyExclusiveGroup,
    SubcommandDef,
    COMMANDS,
    build_all_subparsers,
    get_grouped_commands,
    get_main_examples,
)
from .report_command import ReportCommand

__all__ = [
    "RdstResult",
    "RdstCLI",
    "rdst",
    "HelpCommand",
    "ReportCommand",
    "ArgDef",
    "CommandDef",
    "MutuallyExclusiveGroup",
    "SubcommandDef",
    "COMMANDS",
    "build_all_subparsers",
    "get_grouped_commands",
    "get_main_examples",
]
