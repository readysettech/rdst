"""
Semantic Layer Data Structures

This module defines the data models for rdst's semantic layer, which captures
domain knowledge about database schemas to improve NL-to-SQL generation.

The semantic layer includes:
- Table and column descriptions with business context
- Enum value mappings
- Table relationships
- Business terminology definitions
- Computed metrics

Storage: Per-target YAML files in ~/.rdst/semantic-layer/<target>.yaml
"""

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone
from pathlib import Path
import yaml


def _utcnow() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


@dataclass
class ColumnAnnotation:
    """Annotation for a database column with business context."""

    name: str
    description: str = ""
    data_type: str = ""  # string, int, enum, timestamp, boolean, etc.
    unit: str = ""  # USD, cents, milliseconds, etc.
    timezone: str = ""  # UTC, America/New_York, etc.
    nullable_meaning: str = ""  # "not applicable" vs "unknown"

    # Enum mappings: value -> human-readable meaning
    enum_values: dict[str, str] = field(default_factory=dict)

    # Common filter hint (e.g., "status = 'A'" for active records)
    default_filter: str = ""

    # Privacy/security marker
    is_pii: bool = False

    # Data quality notes
    quality_notes: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary for YAML serialization."""
        result = {}
        if self.description:
            result['description'] = self.description
        if self.data_type:
            result['type'] = self.data_type
        if self.unit:
            result['unit'] = self.unit
        if self.timezone:
            result['timezone'] = self.timezone
        if self.nullable_meaning:
            result['nullable_meaning'] = self.nullable_meaning
        if self.enum_values:
            result['enum_values'] = self.enum_values
        if self.default_filter:
            result['default_filter'] = self.default_filter
        if self.is_pii:
            result['is_pii'] = self.is_pii
        if self.quality_notes:
            result['quality_notes'] = self.quality_notes
        return result

    @classmethod
    def from_dict(cls, name: str, data: dict) -> 'ColumnAnnotation':
        """Create from dictionary loaded from YAML."""
        return cls(
            name=name,
            description=data.get('description', ''),
            data_type=data.get('type', ''),
            unit=data.get('unit', ''),
            timezone=data.get('timezone', ''),
            nullable_meaning=data.get('nullable_meaning', ''),
            enum_values=data.get('enum_values', {}),
            default_filter=data.get('default_filter', ''),
            is_pii=data.get('is_pii', False),
            quality_notes=data.get('quality_notes', '')
        )


@dataclass
class Relationship:
    """Defines a relationship between two tables."""

    target_table: str
    join_pattern: str  # SQL join condition, e.g., "users.id = orders.user_id"
    relationship_type: str = "one_to_many"  # one_to_one, one_to_many, many_to_many
    description: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary for YAML serialization."""
        result = {
            'target': self.target_table,
            'join': self.join_pattern,
            'type': self.relationship_type
        }
        if self.description:
            result['description'] = self.description
        return result

    @classmethod
    def from_dict(cls, data: dict) -> 'Relationship':
        """Create from dictionary loaded from YAML."""
        return cls(
            target_table=data.get('target', ''),
            join_pattern=data.get('join', ''),
            relationship_type=data.get('type', 'one_to_many'),
            description=data.get('description', '')
        )


@dataclass
class TableAnnotation:
    """Annotation for a database table with business context."""

    name: str
    description: str = ""
    business_context: str = ""  # When/why this table is used
    row_estimate: str = ""  # e.g., "1.2M", "~50K"
    data_freshness: str = ""  # real-time, hourly, daily batch

    # Column annotations
    columns: dict[str, ColumnAnnotation] = field(default_factory=dict)

    # Relationships to other tables
    relationships: list[Relationship] = field(default_factory=list)

    # Access pattern hints
    access_hints: list[str] = field(default_factory=list)  # e.g., "Always filter by tenant_id"

    def to_dict(self) -> dict:
        """Convert to dictionary for YAML serialization."""
        result = {}
        if self.description:
            result['description'] = self.description
        if self.business_context:
            result['business_context'] = self.business_context
        if self.row_estimate:
            result['row_estimate'] = self.row_estimate
        if self.data_freshness:
            result['data_freshness'] = self.data_freshness

        if self.columns:
            result['columns'] = {
                name: col.to_dict()
                for name, col in self.columns.items()
                if col.to_dict()  # Only include non-empty
            }

        if self.relationships:
            result['relationships'] = [rel.to_dict() for rel in self.relationships]

        if self.access_hints:
            result['access_hints'] = self.access_hints

        return result

    @classmethod
    def from_dict(cls, name: str, data: dict) -> 'TableAnnotation':
        """Create from dictionary loaded from YAML."""
        columns = {}
        for col_name, col_data in data.get('columns', {}).items():
            columns[col_name] = ColumnAnnotation.from_dict(col_name, col_data)

        relationships = [
            Relationship.from_dict(rel_data)
            for rel_data in data.get('relationships', [])
        ]

        return cls(
            name=name,
            description=data.get('description', ''),
            business_context=data.get('business_context', ''),
            row_estimate=data.get('row_estimate', ''),
            data_freshness=data.get('data_freshness', ''),
            columns=columns,
            relationships=relationships,
            access_hints=data.get('access_hints', [])
        )


@dataclass
class Terminology:
    """Business terminology definition with SQL pattern."""

    term: str
    definition: str  # Human-readable definition
    sql_pattern: str  # SQL expression that implements this term
    synonyms: list[str] = field(default_factory=list)  # Alternative names
    examples: list[str] = field(default_factory=list)  # Example usage
    tables_used: list[str] = field(default_factory=list)  # Tables referenced by this term

    def to_dict(self) -> dict:
        """Convert to dictionary for YAML serialization."""
        result = {
            'definition': self.definition,
            'sql_pattern': self.sql_pattern
        }
        if self.synonyms:
            result['synonyms'] = self.synonyms
        if self.examples:
            result['examples'] = self.examples
        if self.tables_used:
            result['tables_used'] = self.tables_used
        return result

    @classmethod
    def from_dict(cls, term: str, data: dict) -> 'Terminology':
        """Create from dictionary loaded from YAML."""
        return cls(
            term=term,
            definition=data.get('definition', ''),
            sql_pattern=data.get('sql_pattern', ''),
            synonyms=data.get('synonyms', []),
            examples=data.get('examples', []),
            tables_used=data.get('tables_used', [])
        )


@dataclass
class Metric:
    """Computed metric definition with SQL expression."""

    name: str
    definition: str  # Human-readable definition
    sql: str  # SQL expression to compute this metric
    unit: str = ""  # USD, count, percentage, etc.
    aggregation_type: str = ""  # sum, count, avg, etc.

    def to_dict(self) -> dict:
        """Convert to dictionary for YAML serialization."""
        result = {
            'definition': self.definition,
            'sql': self.sql
        }
        if self.unit:
            result['unit'] = self.unit
        if self.aggregation_type:
            result['aggregation_type'] = self.aggregation_type
        return result

    @classmethod
    def from_dict(cls, name: str, data: dict) -> 'Metric':
        """Create from dictionary loaded from YAML."""
        return cls(
            name=name,
            definition=data.get('definition', ''),
            sql=data.get('sql', ''),
            unit=data.get('unit', ''),
            aggregation_type=data.get('aggregation_type', '')
        )


@dataclass
class Extension:
    """PostgreSQL extension with type information.

    Extensions like pgx_ulid, postgis, pgvector provide custom types
    that are critical for understanding type compatibility in queries.
    """

    name: str
    version: str = ""
    description: str = ""
    # Types provided by this extension (e.g., ulid, geometry, vector)
    types_provided: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for YAML serialization."""
        result = {}
        if self.version:
            result['version'] = self.version
        if self.description:
            result['description'] = self.description
        if self.types_provided:
            result['types_provided'] = self.types_provided
        return result

    @classmethod
    def from_dict(cls, name: str, data: dict) -> 'Extension':
        """Create from dictionary loaded from YAML."""
        return cls(
            name=name,
            version=data.get('version', ''),
            description=data.get('description', ''),
            types_provided=data.get('types_provided', [])
        )


@dataclass
class CustomType:
    """Custom database type (enum, domain, or extension type).

    Captures types that are not built-in PostgreSQL types, which is
    essential for LLM to understand type compatibility and avoid
    incorrect cast suggestions.
    """

    name: str
    type_category: str = ""  # enum, domain, base (extension type), composite
    base_type: str = ""  # For domains, the underlying type
    enum_values: list[str] = field(default_factory=list)  # For enum types
    description: str = ""
    # Extension that provides this type (if any)
    extension: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary for YAML serialization."""
        result = {'category': self.type_category}
        if self.base_type:
            result['base_type'] = self.base_type
        if self.enum_values:
            result['enum_values'] = self.enum_values
        if self.description:
            result['description'] = self.description
        if self.extension:
            result['extension'] = self.extension
        return result

    @classmethod
    def from_dict(cls, name: str, data: dict) -> 'CustomType':
        """Create from dictionary loaded from YAML."""
        return cls(
            name=name,
            type_category=data.get('category', ''),
            base_type=data.get('base_type', ''),
            enum_values=data.get('enum_values', []),
            description=data.get('description', ''),
            extension=data.get('extension', '')
        )


@dataclass
class SemanticLayer:
    """
    Complete semantic layer for a database target.

    Contains all domain knowledge needed to improve NL-to-SQL generation:
    - Table and column descriptions
    - Enum mappings
    - Relationships
    - Business terminology
    - Computed metrics
    """

    target: str
    version: int = 1
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    # Core annotations
    tables: dict[str, TableAnnotation] = field(default_factory=dict)
    terminology: dict[str, Terminology] = field(default_factory=dict)
    metrics: dict[str, Metric] = field(default_factory=dict)

    # Database-level metadata (PostgreSQL)
    extensions: dict[str, Extension] = field(default_factory=dict)
    custom_types: dict[str, CustomType] = field(default_factory=dict)

    # Global notes
    notes: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary for YAML serialization."""
        result = {
            'version': self.version,
            'target': self.target,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }

        if self.tables:
            result['tables'] = {
                name: table.to_dict()
                for name, table in self.tables.items()
            }

        if self.terminology:
            result['terminology'] = {
                term: t.to_dict()
                for term, t in self.terminology.items()
            }

        if self.metrics:
            result['metrics'] = {
                name: m.to_dict()
                for name, m in self.metrics.items()
            }

        if self.extensions:
            result['extensions'] = {
                name: ext.to_dict()
                for name, ext in self.extensions.items()
            }

        if self.custom_types:
            result['custom_types'] = {
                name: ct.to_dict()
                for name, ct in self.custom_types.items()
            }

        if self.notes:
            result['notes'] = self.notes

        return result

    @classmethod
    def from_dict(cls, data: dict) -> 'SemanticLayer':
        """Create from dictionary loaded from YAML."""
        tables = {}
        for table_name, table_data in data.get('tables', {}).items():
            tables[table_name] = TableAnnotation.from_dict(table_name, table_data)

        terminology = {}
        for term, term_data in data.get('terminology', {}).items():
            terminology[term] = Terminology.from_dict(term, term_data)

        metrics = {}
        for metric_name, metric_data in data.get('metrics', {}).items():
            metrics[metric_name] = Metric.from_dict(metric_name, metric_data)

        extensions = {}
        for ext_name, ext_data in data.get('extensions', {}).items():
            extensions[ext_name] = Extension.from_dict(ext_name, ext_data)

        custom_types = {}
        for type_name, type_data in data.get('custom_types', {}).items():
            custom_types[type_name] = CustomType.from_dict(type_name, type_data)

        # Parse timestamps
        created_at = _utcnow()
        updated_at = _utcnow()
        if 'created_at' in data:
            try:
                created_at = datetime.fromisoformat(data['created_at'])
            except (ValueError, TypeError):
                pass
        if 'updated_at' in data:
            try:
                updated_at = datetime.fromisoformat(data['updated_at'])
            except (ValueError, TypeError):
                pass

        return cls(
            target=data.get('target', ''),
            version=data.get('version', 1),
            created_at=created_at,
            updated_at=updated_at,
            tables=tables,
            terminology=terminology,
            metrics=metrics,
            extensions=extensions,
            custom_types=custom_types,
            notes=data.get('notes', '')
        )

    def save(self, path: Path) -> None:
        """Save semantic layer to YAML file."""
        self.updated_at = _utcnow()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w') as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, sort_keys=False)

    @classmethod
    def load(cls, path: Path) -> 'SemanticLayer':
        """Load semantic layer from YAML file."""
        if not path.exists():
            raise FileNotFoundError(f"Semantic layer file not found: {path}")

        with open(path, 'r') as f:
            data = yaml.safe_load(f)

        return cls.from_dict(data)

    # Query methods for use by ask3

    def get_table_context(self, table_names: list[str]) -> str:
        """Get formatted context for specified tables."""
        context_parts = []

        for table_name in table_names:
            if table_name not in self.tables:
                continue

            table = self.tables[table_name]
            parts = [f"Table: {table_name}"]

            if table.description:
                parts.append(f"  Description: {table.description}")
            if table.business_context:
                parts.append(f"  Context: {table.business_context}")

            # Column descriptions
            if table.columns:
                parts.append("  Columns:")
                for col_name, col in table.columns.items():
                    col_desc = f"    - {col_name}"
                    if col.description:
                        col_desc += f": {col.description}"
                    if col.enum_values:
                        enum_str = ", ".join(
                            f"{k}={v}" for k, v in col.enum_values.items()
                        )
                        col_desc += f" [{enum_str}]"
                    parts.append(col_desc)

            # Relationships
            if table.relationships:
                parts.append("  Relationships:")
                for rel in table.relationships:
                    parts.append(f"    - {rel.relationship_type} to {rel.target_table}: {rel.join_pattern}")

            context_parts.append("\n".join(parts))

        return "\n\n".join(context_parts)

    def get_terminology_matches(self, text: str) -> list[Terminology]:
        """Find terminology that matches text (case-insensitive)."""
        text_lower = text.lower()
        matches = []

        for term, terminology in self.terminology.items():
            # Check main term
            if term.lower() in text_lower:
                matches.append(terminology)
                continue

            # Check synonyms
            for synonym in terminology.synonyms:
                if synonym.lower() in text_lower:
                    matches.append(terminology)
                    break

        return matches

    def get_enum_values(self, table_name: str, column_name: str) -> dict[str, str]:
        """Get enum value mappings for a column."""
        if table_name not in self.tables:
            return {}

        table = self.tables[table_name]
        if column_name not in table.columns:
            return {}

        return table.columns[column_name].enum_values

    def get_column_description(self, table_name: str, column_name: str) -> str:
        """Get description for a specific column."""
        if table_name not in self.tables:
            return ""

        table = self.tables[table_name]
        if column_name not in table.columns:
            return ""

        return table.columns[column_name].description

    def get_metrics_context(self) -> str:
        """Get formatted context for all metrics."""
        if not self.metrics:
            return ""

        parts = ["Available Metrics:"]
        for name, metric in self.metrics.items():
            parts.append(f"  - {name}: {metric.definition}")
            parts.append(f"    SQL: {metric.sql}")

        return "\n".join(parts)

    def get_extensions_context(self) -> str:
        """Get formatted context for installed extensions and custom types.

        This is critical for LLM to understand type compatibility and avoid
        incorrect recommendations like 'cast ULID to integer'.
        """
        parts = []

        if self.extensions:
            parts.append("Installed Extensions:")
            for name, ext in self.extensions.items():
                if ext.description:
                    parts.append(f"  - {name} v{ext.version}: {ext.description}")
                else:
                    parts.append(f"  - {name} v{ext.version}")
                if ext.types_provided:
                    parts.append(f"    Types: {', '.join(ext.types_provided)}")

        if self.custom_types:
            if parts:
                parts.append("")
            parts.append("Custom Types:")
            for name, ct in self.custom_types.items():
                if ct.type_category == 'enum' and ct.enum_values:
                    values_str = ', '.join(ct.enum_values[:5])
                    if len(ct.enum_values) > 5:
                        values_str += f"... ({len(ct.enum_values)} total)"
                    parts.append(f"  - {name} (enum): [{values_str}]")
                elif ct.type_category == 'domain' and ct.base_type:
                    parts.append(f"  - {name} (domain over {ct.base_type})")
                elif ct.type_category == 'base':
                    desc = ct.description or "Compare with same type only"
                    parts.append(f"  - {name} (extension type): {desc}")
                else:
                    parts.append(f"  - {name} ({ct.type_category})")

        return "\n".join(parts) if parts else ""

    def add_terminology(self, term: str, definition: str, sql_pattern: str,
                       synonyms: list[str] = None, tables_used: list[str] = None) -> None:
        """Add or update a terminology entry."""
        self.terminology[term] = Terminology(
            term=term,
            definition=definition,
            sql_pattern=sql_pattern,
            synonyms=synonyms or [],
            tables_used=tables_used or []
        )
        self.updated_at = _utcnow()

    def add_table_description(self, table_name: str, description: str,
                             business_context: str = "") -> None:
        """Add or update a table description."""
        if table_name not in self.tables:
            self.tables[table_name] = TableAnnotation(name=table_name)

        self.tables[table_name].description = description
        if business_context:
            self.tables[table_name].business_context = business_context
        self.updated_at = _utcnow()

    def add_column_description(self, table_name: str, column_name: str,
                              description: str, **kwargs) -> None:
        """Add or update a column description."""
        if table_name not in self.tables:
            self.tables[table_name] = TableAnnotation(name=table_name)

        table = self.tables[table_name]
        if column_name not in table.columns:
            table.columns[column_name] = ColumnAnnotation(name=column_name)

        col = table.columns[column_name]
        col.description = description

        # Update optional fields
        for key, value in kwargs.items():
            if hasattr(col, key):
                setattr(col, key, value)

        self.updated_at = _utcnow()

    def add_enum_values(self, table_name: str, column_name: str,
                       enum_values: dict[str, str]) -> None:
        """Add enum value mappings for a column."""
        if table_name not in self.tables:
            self.tables[table_name] = TableAnnotation(name=table_name)

        table = self.tables[table_name]
        if column_name not in table.columns:
            table.columns[column_name] = ColumnAnnotation(name=column_name)

        table.columns[column_name].enum_values.update(enum_values)
        self.updated_at = _utcnow()
