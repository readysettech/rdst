"""
Semantic Layer Manager

Handles loading, saving, and querying semantic layer data.
Provides the main interface for other rdst components to access domain knowledge.
"""

from pathlib import Path
from typing import Optional
import os

from ..data_structures.semantic_layer import (
    SemanticLayer,
    TableAnnotation,
    ColumnAnnotation,
    Terminology,
    Metric,
    Relationship
)


class SemanticLayerManager:
    """
    Manager for semantic layer operations.

    Handles:
    - Loading/saving semantic layer files
    - CRUD operations for annotations
    - Query methods for ask3 integration
    - Progressive learning updates
    """

    def __init__(self, base_dir: Optional[Path] = None):
        """
        Initialize the semantic layer manager.

        Args:
            base_dir: Base directory for semantic layer files.
                      Defaults to ~/.rdst/semantic-layer/
        """
        if base_dir is None:
            base_dir = Path.home() / ".rdst" / "semantic-layer"

        self.base_dir = Path(base_dir)
        self._cache: dict[str, SemanticLayer] = {}

    def get_path(self, target: str) -> Path:
        """Get the file path for a target's semantic layer."""
        return self.base_dir / f"{target}.yaml"

    def exists(self, target: str) -> bool:
        """Check if a semantic layer exists for a target."""
        return self.get_path(target).exists()

    def list_targets(self) -> list[str]:
        """List all targets with semantic layers."""
        if not self.base_dir.exists():
            return []

        targets = []
        for path in self.base_dir.glob("*.yaml"):
            targets.append(path.stem)

        return sorted(targets)

    def load(self, target: str, use_cache: bool = True) -> SemanticLayer:
        """
        Load semantic layer for a target.

        Args:
            target: Target database name
            use_cache: Whether to use cached version if available

        Returns:
            SemanticLayer object

        Raises:
            FileNotFoundError: If no semantic layer exists for target
        """
        if use_cache and target in self._cache:
            return self._cache[target]

        path = self.get_path(target)
        layer = SemanticLayer.load(path)

        self._cache[target] = layer
        return layer

    def load_or_create(self, target: str) -> SemanticLayer:
        """
        Load existing semantic layer or create a new empty one.

        Args:
            target: Target database name

        Returns:
            SemanticLayer object (new or existing)
        """
        if self.exists(target):
            return self.load(target)

        layer = SemanticLayer(target=target)
        self._cache[target] = layer
        return layer

    def save(self, layer: SemanticLayer) -> None:
        """
        Save semantic layer to disk.

        Args:
            layer: SemanticLayer to save
        """
        path = self.get_path(layer.target)
        layer.save(path)
        self._cache[layer.target] = layer

    def delete(self, target: str) -> bool:
        """
        Delete semantic layer for a target.

        Args:
            target: Target database name

        Returns:
            True if deleted, False if not found
        """
        path = self.get_path(target)
        if not path.exists():
            return False

        path.unlink()
        if target in self._cache:
            del self._cache[target]

        return True

    def clear_cache(self) -> None:
        """Clear the in-memory cache."""
        self._cache.clear()

    # High-level operations for CLI commands

    def add_table(self, target: str, table_name: str, description: str,
                  business_context: str = "", row_estimate: str = "") -> None:
        """Add or update a table annotation."""
        layer = self.load_or_create(target)

        if table_name not in layer.tables:
            layer.tables[table_name] = TableAnnotation(name=table_name)

        table = layer.tables[table_name]
        table.description = description
        if business_context:
            table.business_context = business_context
        if row_estimate:
            table.row_estimate = row_estimate

        self.save(layer)

    def add_column(self, target: str, table_name: str, column_name: str,
                   description: str, **kwargs) -> None:
        """Add or update a column annotation."""
        layer = self.load_or_create(target)
        layer.add_column_description(table_name, column_name, description, **kwargs)
        self.save(layer)

    def add_enum(self, target: str, table_name: str, column_name: str,
                 enum_values: dict[str, str]) -> None:
        """Add enum value mappings for a column."""
        layer = self.load_or_create(target)
        layer.add_enum_values(table_name, column_name, enum_values)
        self.save(layer)

    def add_terminology(self, target: str, term: str, definition: str,
                       sql_pattern: str, synonyms: list[str] = None,
                       tables_used: list[str] = None) -> None:
        """Add or update a terminology entry."""
        layer = self.load_or_create(target)
        layer.add_terminology(term, definition, sql_pattern, synonyms, tables_used)
        self.save(layer)

    def add_relationship(self, target: str, source_table: str, target_table: str,
                        join_pattern: str, relationship_type: str = "one_to_many") -> None:
        """Add a relationship between tables."""
        layer = self.load_or_create(target)

        if source_table not in layer.tables:
            layer.tables[source_table] = TableAnnotation(name=source_table)

        # Check if relationship already exists
        existing = [r for r in layer.tables[source_table].relationships
                   if r.target_table == target_table]
        if existing:
            # Update existing
            existing[0].join_pattern = join_pattern
            existing[0].relationship_type = relationship_type
        else:
            # Add new
            layer.tables[source_table].relationships.append(
                Relationship(
                    target_table=target_table,
                    join_pattern=join_pattern,
                    relationship_type=relationship_type
                )
            )

        self.save(layer)

    def add_metric(self, target: str, name: str, definition: str,
                  sql: str, unit: str = "") -> None:
        """Add or update a metric definition."""
        layer = self.load_or_create(target)
        layer.metrics[name] = Metric(
            name=name,
            definition=definition,
            sql=sql,
            unit=unit
        )
        self.save(layer)

    # Query methods for ask3 integration

    def get_context_for_tables(self, target: str, table_names: list[str]) -> str:
        """
        Get formatted semantic context for specified tables.

        Used by ask3 to inject domain knowledge into LLM prompts.

        Args:
            target: Target database name
            table_names: List of table names to get context for

        Returns:
            Formatted string with table descriptions, column meanings, etc.
        """
        if not self.exists(target):
            return ""

        layer = self.load(target)
        return layer.get_table_context(table_names)

    def find_terminology(self, target: str, text: str) -> list[Terminology]:
        """
        Find terminology that matches text.

        Used by ask3 to resolve business terms in user questions.

        Args:
            target: Target database name
            text: User question or text to search

        Returns:
            List of matching Terminology objects
        """
        if not self.exists(target):
            return []

        layer = self.load(target)
        return layer.get_terminology_matches(text)

    def get_enum_values(self, target: str, table_name: str,
                       column_name: str) -> dict[str, str]:
        """
        Get enum value mappings for a column.

        Args:
            target: Target database name
            table_name: Table name
            column_name: Column name

        Returns:
            Dict of value -> human-readable meaning
        """
        if not self.exists(target):
            return {}

        layer = self.load(target)
        return layer.get_enum_values(table_name, column_name)

    def get_full_context(self, target: str, user_question: str,
                        relevant_tables: list[str] = None) -> str:
        """
        Get complete semantic context for ask3 SQL generation.

        Combines:
        - Table/column descriptions for relevant tables
        - Terminology matches from user question
        - Metric definitions

        Args:
            target: Target database name
            user_question: The user's natural language question
            relevant_tables: List of tables identified as relevant

        Returns:
            Formatted context string for LLM prompt
        """
        if not self.exists(target):
            return ""

        layer = self.load(target)
        parts = []

        # Table context
        if relevant_tables:
            table_context = layer.get_table_context(relevant_tables)
            if table_context:
                parts.append("=== Schema Context ===")
                parts.append(table_context)

        # Terminology matches
        terminology = layer.get_terminology_matches(user_question)
        if terminology:
            parts.append("\n=== Business Terminology ===")
            for term in terminology:
                parts.append(f"'{term.term}': {term.definition}")
                parts.append(f"  SQL: {term.sql_pattern}")

        # Metrics
        metrics_context = layer.get_metrics_context()
        if metrics_context:
            parts.append("\n" + metrics_context)

        return "\n".join(parts)

    # Progressive learning support

    def learn_terminology(self, target: str, term: str, definition: str,
                         sql_pattern: str, synonyms: list[str] = None) -> None:
        """
        Learn a new terminology from user interaction.

        Called by ask3 when user explains a business term during conversation.

        Args:
            target: Target database name
            term: The business term (e.g., "churned")
            definition: Human-readable definition
            sql_pattern: SQL expression that implements this term
            synonyms: Alternative names for this term
        """
        self.add_terminology(target, term, definition, sql_pattern, synonyms)

    def learn_enum_value(self, target: str, table_name: str, column_name: str,
                        value: str, meaning: str) -> None:
        """
        Learn an enum value mapping from user interaction.

        Called by ask3 when user explains what a status code means.

        Args:
            target: Target database name
            table_name: Table name
            column_name: Column name
            value: The actual value in the database
            meaning: Human-readable meaning
        """
        layer = self.load_or_create(target)
        layer.add_enum_values(table_name, column_name, {value: meaning})
        self.save(layer)

    # Export/import

    def export_yaml(self, target: str) -> str:
        """
        Export semantic layer as YAML string.

        Args:
            target: Target database name

        Returns:
            YAML string representation
        """
        import yaml

        if not self.exists(target):
            raise FileNotFoundError(f"No semantic layer for target: {target}")

        layer = self.load(target)
        return yaml.dump(layer.to_dict(), default_flow_style=False, sort_keys=False)

    def get_summary(self, target: str) -> dict:
        """
        Get summary statistics for a semantic layer.

        Args:
            target: Target database name

        Returns:
            Dict with counts of tables, columns, terminology, etc.
        """
        if not self.exists(target):
            return {
                'exists': False,
                'tables': 0,
                'columns': 0,
                'terminology': 0,
                'metrics': 0
            }

        layer = self.load(target)

        total_columns = sum(
            len(table.columns) for table in layer.tables.values()
        )

        total_relationships = sum(
            len(table.relationships) for table in layer.tables.values()
        )

        return {
            'exists': True,
            'tables': len(layer.tables),
            'columns': total_columns,
            'relationships': total_relationships,
            'terminology': len(layer.terminology),
            'metrics': len(layer.metrics),
            'updated_at': layer.updated_at.isoformat()
        }
