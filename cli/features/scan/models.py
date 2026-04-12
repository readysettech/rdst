"""Scan feature models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class ScanPhase(str, Enum):
    CONFIG = "config"
    DISCOVERY = "discovery"
    EXTRACTION = "extraction"
    CONVERSION = "conversion"
    REGISTRY = "registry"
    ANALYSIS = "analysis"


@dataclass
class ScanInput:
    """Input for scan service."""

    directory: str
    target: str
    source: str = "web"


@dataclass
class ScanOptions:
    """Options for scan service execution."""

    analyze: bool = False
    shallow: bool = False
    dry_run: bool = False
    diff: Optional[str] = None
    check: bool = False
    warn_threshold: int = 60
    fail_threshold: int = 40
    file_pattern: Optional[str] = None
    nosave: bool = False
    sequential: bool = False


@dataclass(frozen=True)
class ScannedFile:
    """Discovered file with ORM patterns."""

    file: str
    orms: list[str]
    lines: int


@dataclass(frozen=True)
class ScannedQuery:
    """Extracted query information."""

    file: str
    function: Optional[str]
    klass: Optional[str]
    orm_code: str
    snippet_hash: str
    terminal_method: Optional[str]
    start_line: int
    end_line: int
    imports_builder: bool
    orm_type: Optional[str]
    sql: str
    issues: list[str]
    status: Optional[str] = None
    skip_reason: Optional[str] = None
    hash: str = ""
