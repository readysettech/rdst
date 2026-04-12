"""Schema feature models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SchemaStatus:
    """Status summary for a semantic layer."""

    target: str
    exists: bool
    tables: int
    columns: int
    relationships: int
    terminology: int
    updated_at: Optional[str]


@dataclass
class SchemaTableColumn:
    """Column annotation details."""

    name: str
    data_type: Optional[str]
    description: Optional[str]
    unit: Optional[str] = None
    is_pii: bool = False
    enum_values: Optional[dict[str, str]] = None
    value_pattern: Optional[str] = None
    null_fraction: Optional[float] = None
    distinct_count: Optional[int] = None


@dataclass
class SchemaTableRelationship:
    """Relationship from one table to another."""

    target_table: str
    relationship_type: str
    join_pattern: str


@dataclass
class SchemaTable:
    """Table annotation details."""

    name: str
    description: Optional[str]
    business_context: Optional[str]
    row_estimate: Optional[str]
    columns: list[SchemaTableColumn]
    relationships: list[SchemaTableRelationship]


@dataclass
class SchemaTerminology:
    """Business terminology entry."""

    term: str
    definition: str
    sql_pattern: str
    synonyms: list[str]


@dataclass
class SchemaMetric:
    """Metric definition."""

    name: str
    definition: str
    sql: str


@dataclass
class SchemaExtension:
    """Installed database extension."""

    name: str
    version: str
    description: Optional[str]
    types_provided: list[str]


@dataclass
class SchemaCustomType:
    """Custom database type."""

    name: str
    type_category: str
    base_type: Optional[str]
    enum_values: Optional[list[str]]
    description: Optional[str]


@dataclass
class SchemaDetails:
    """Full semantic layer details."""

    target: str
    tables: list[SchemaTable]
    terminology: list[SchemaTerminology]
    extensions: list[SchemaExtension]
    custom_types: list[SchemaCustomType]
    metrics: list[SchemaMetric]


@dataclass
class SchemaTargetSummary:
    """Summary for a target semantic layer."""

    name: str
    tables: int
    terminology: int
    updated_at: Optional[str]


@dataclass
class SchemaTargetList:
    """List of targets with semantic layers."""

    targets: list[SchemaTargetSummary]


@dataclass
class SchemaInitOptions:
    """Options for schema init."""

    enum_threshold: int = 20
    force: bool = False
    sample_enums: bool = True


@dataclass
class SchemaInitResult:
    """Result of schema init."""

    success: bool
    target: str
    tables: int
    columns: int
    relationships: int
    enum_columns: list[str]
    path: Optional[str] = None
    error: Optional[str] = None


@dataclass
class SchemaExportResult:
    """Result of schema export."""

    success: bool
    format: str
    content: str
    error: Optional[str] = None


@dataclass
class SchemaDeleteResult:
    """Result of schema delete."""

    success: bool
    target: str
    error: Optional[str] = None


@dataclass
class SchemaUpdateResult:
    """Result of schema update operations."""

    success: bool
    message: str
    error: Optional[str] = None
