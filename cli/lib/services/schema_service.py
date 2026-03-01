"""Service for semantic layer management.

Provides a stateless interface for semantic layer CRUD operations,
used by both CLI and Web API.
"""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Dict, List, Optional

from ..semantic_layer.manager import SemanticLayerManager
from ..semantic_layer.introspector import SchemaIntrospector

from .anthropic_env import has_anthropic_api_key
from .types import (
    SchemaStatus,
    SchemaDetails,
    SchemaTable,
    SchemaTableColumn,
    SchemaTableRelationship,
    SchemaTerminology,
    SchemaMetric,
    SchemaExtension,
    SchemaCustomType,
    SchemaTargetSummary,
    SchemaTargetList,
    SchemaInitOptions,
    SchemaInitResult,
    SchemaExportResult,
    SchemaDeleteResult,
    SchemaUpdateResult,
    SchemaStatusEvent,
    SchemaCompleteEvent,
    SchemaErrorEvent,
    SchemaEvent,
)


class SchemaService:
    """Stateless service for semantic layer management."""

    def __init__(self):
        self._manager = SemanticLayerManager()

    def get_status(self, target: str) -> SchemaStatus:
        """Check if semantic layer exists and get summary.

        Args:
            target: Target database name

        Returns:
            SchemaStatus with existence flag and summary stats
        """
        if not self._manager.exists(target):
            return SchemaStatus(
                target=target,
                exists=False,
                tables=0,
                columns=0,
                relationships=0,
                terminology=0,
                updated_at=None,
            )

        summary = self._manager.get_summary(target)
        return SchemaStatus(
            target=target,
            exists=True,
            tables=summary.get("tables", 0),
            columns=summary.get("columns", 0),
            relationships=summary.get("relationships", 0),
            terminology=summary.get("terminology", 0),
            updated_at=summary.get("updated_at"),
        )

    def get_schema(
        self, target: str, table_name: Optional[str] = None
    ) -> Optional[SchemaDetails]:
        """Load full semantic layer or single table.

        Args:
            target: Target database name
            table_name: Optional specific table to load

        Returns:
            SchemaDetails or None if not found
        """
        if not self._manager.exists(target):
            return None

        layer = self._manager.load(target)

        # Convert tables
        tables_to_convert = layer.tables
        if table_name:
            if table_name not in layer.tables:
                return None
            tables_to_convert = {table_name: layer.tables[table_name]}

        tables = []
        for name, table in tables_to_convert.items():
            columns = [
                SchemaTableColumn(
                    name=col_name,
                    data_type=col.data_type,
                    description=col.description,
                    unit=col.unit,
                    is_pii=col.is_pii or False,
                    enum_values=col.enum_values,
                    value_pattern=col.value_pattern or None,
                )
                for col_name, col in table.columns.items()
            ]

            relationships = [
                SchemaTableRelationship(
                    target_table=rel.target_table,
                    relationship_type=rel.relationship_type,
                    join_pattern=rel.join_pattern,
                )
                for rel in table.relationships
            ]

            tables.append(
                SchemaTable(
                    name=name,
                    description=table.description,
                    business_context=table.business_context,
                    row_estimate=table.row_estimate,
                    columns=columns,
                    relationships=relationships,
                )
            )

        # Convert terminology
        terminology = [
            SchemaTerminology(
                term=term,
                definition=t.definition,
                sql_pattern=t.sql_pattern,
                synonyms=t.synonyms or [],
            )
            for term, t in layer.terminology.items()
        ]

        # Convert metrics
        metrics = [
            SchemaMetric(
                name=name,
                definition=m.definition,
                sql=m.sql,
            )
            for name, m in layer.metrics.items()
        ]

        # Convert extensions
        extensions = [
            SchemaExtension(
                name=name,
                version=ext.version or "",
                description=ext.description,
                types_provided=ext.types_provided or [],
            )
            for name, ext in layer.extensions.items()
        ]

        # Convert custom types
        custom_types = [
            SchemaCustomType(
                name=name,
                type_category=ct.type_category or "base",
                base_type=ct.base_type,
                enum_values=ct.enum_values,
                description=ct.description,
            )
            for name, ct in layer.custom_types.items()
        ]

        return SchemaDetails(
            target=target,
            tables=tables,
            terminology=terminology,
            extensions=extensions,
            custom_types=custom_types,
            metrics=metrics,
        )

    def list_targets(self) -> SchemaTargetList:
        """List all targets with semantic layers.

        Returns:
            SchemaTargetList with target summaries
        """
        target_names = self._manager.list_targets()

        targets = []
        for name in target_names:
            summary = self._manager.get_summary(name)
            targets.append(
                SchemaTargetSummary(
                    name=name,
                    tables=summary.get("tables", 0),
                    terminology=summary.get("terminology", 0),
                    updated_at=summary.get("updated_at"),
                )
            )

        return SchemaTargetList(targets=targets)

    def init(
        self,
        target: str,
        target_config: Dict[str, Any],
        options: Optional[SchemaInitOptions] = None,
    ) -> SchemaInitResult:
        """Initialize semantic layer by introspecting database.

        Args:
            target: Target name for the semantic layer
            target_config: Database configuration dict
            options: Init options (enum_threshold, force, sample_enums)

        Returns:
            SchemaInitResult with success status and summary
        """
        options = options or SchemaInitOptions()

        # Check if already exists
        if self._manager.exists(target) and not options.force:
            return SchemaInitResult(
                success=False,
                target=target,
                tables=0,
                columns=0,
                relationships=0,
                enum_columns=[],
                error=f"Semantic layer already exists for '{target}'. Use force=True to overwrite.",
            )

        try:
            # Introspect database
            introspector = SchemaIntrospector(target_config)
            layer = introspector.introspect(
                target_name=target,
                enum_threshold=options.enum_threshold,
                sample_enums=options.sample_enums,
            )

            # Save the layer
            self._manager.save(layer)

            # Build summary
            total_columns = sum(len(t.columns) for t in layer.tables.values())
            total_relationships = sum(
                len(t.relationships) for t in layer.tables.values()
            )

            # Count enum columns
            enum_columns = []
            for table_name, table in layer.tables.items():
                for col_name, col in table.columns.items():
                    if col.enum_values:
                        enum_columns.append(f"{table_name}.{col_name}")

            return SchemaInitResult(
                success=True,
                target=target,
                tables=len(layer.tables),
                columns=total_columns,
                relationships=total_relationships,
                enum_columns=enum_columns,
                path=str(self._manager.get_path(target)),
            )

        except ConnectionError as e:
            return SchemaInitResult(
                success=False,
                target=target,
                tables=0,
                columns=0,
                relationships=0,
                enum_columns=[],
                error=f"Database connection failed: {e}",
            )
        except ValueError as e:
            return SchemaInitResult(
                success=False,
                target=target,
                tables=0,
                columns=0,
                relationships=0,
                enum_columns=[],
                error=str(e),
            )
        except Exception as e:
            return SchemaInitResult(
                success=False,
                target=target,
                tables=0,
                columns=0,
                relationships=0,
                enum_columns=[],
                error=f"Failed to initialize semantic layer: {e}",
            )

    def export(self, target: str, format: str = "yaml") -> SchemaExportResult:
        """Export semantic layer as YAML or JSON.

        Args:
            target: Target database name
            format: Export format ('yaml' or 'json')

        Returns:
            SchemaExportResult with content string
        """
        if not self._manager.exists(target):
            return SchemaExportResult(
                success=False,
                format=format,
                content="",
                error=f"No semantic layer found for target '{target}'",
            )

        try:
            if format == "yaml":
                content = self._manager.export_yaml(target)
            elif format == "json":
                layer = self._manager.load(target)
                content = json.dumps(layer.to_dict(), indent=2, default=str)
            else:
                return SchemaExportResult(
                    success=False,
                    format=format,
                    content="",
                    error=f"Unknown format: {format}. Use 'yaml' or 'json'.",
                )

            return SchemaExportResult(
                success=True,
                format=format,
                content=content,
            )
        except Exception as e:
            return SchemaExportResult(
                success=False,
                format=format,
                content="",
                error=f"Export failed: {e}",
            )

    def delete(self, target: str) -> SchemaDeleteResult:
        """Delete semantic layer for a target.

        Args:
            target: Target database name

        Returns:
            SchemaDeleteResult with success status
        """
        if not self._manager.exists(target):
            return SchemaDeleteResult(
                success=False,
                target=target,
                error=f"No semantic layer found for target '{target}'",
            )

        success = self._manager.delete(target)

        if success:
            return SchemaDeleteResult(success=True, target=target)
        else:
            return SchemaDeleteResult(
                success=False,
                target=target,
                error=f"Failed to delete semantic layer for '{target}'",
            )

    def add_table(
        self,
        target: str,
        table_name: str,
        description: str,
        business_context: str = "",
        row_estimate: str = "",
    ) -> SchemaUpdateResult:
        """Add or update a table annotation.

        Args:
            target: Target database name
            table_name: Name of the table
            description: Table description
            business_context: Optional business context
            row_estimate: Optional row count estimate

        Returns:
            SchemaUpdateResult with success status
        """
        try:
            self._manager.add_table(
                target, table_name, description, business_context, row_estimate
            )
            return SchemaUpdateResult(
                success=True,
                message=f"Added table '{table_name}' to semantic layer for '{target}'",
            )
        except Exception as e:
            return SchemaUpdateResult(
                success=False,
                message="",
                error=f"Failed to add table: {e}",
            )

    def add_column(
        self,
        target: str,
        table_name: str,
        column_name: str,
        description: str,
        data_type: Optional[str] = None,
        unit: Optional[str] = None,
        is_pii: bool = False,
    ) -> SchemaUpdateResult:
        """Add or update a column annotation.

        Args:
            target: Target database name
            table_name: Name of the table
            column_name: Name of the column
            description: Column description
            data_type: Optional data type
            unit: Optional unit (e.g., "USD", "kg")
            is_pii: Whether column contains PII

        Returns:
            SchemaUpdateResult with success status
        """
        try:
            kwargs = {}
            if data_type:
                kwargs["data_type"] = data_type
            if unit:
                kwargs["unit"] = unit
            if is_pii:
                kwargs["is_pii"] = is_pii

            self._manager.add_column(
                target, table_name, column_name, description, **kwargs
            )
            return SchemaUpdateResult(
                success=True,
                message=f"Added column '{table_name}.{column_name}'",
            )
        except Exception as e:
            return SchemaUpdateResult(
                success=False,
                message="",
                error=f"Failed to add column: {e}",
            )

    def add_enum(
        self,
        target: str,
        table_name: str,
        column_name: str,
        enum_values: Dict[str, str],
    ) -> SchemaUpdateResult:
        """Add enum value mappings for a column.

        Args:
            target: Target database name
            table_name: Name of the table
            column_name: Name of the column
            enum_values: Dict of value -> meaning

        Returns:
            SchemaUpdateResult with success status
        """
        try:
            self._manager.add_enum(target, table_name, column_name, enum_values)
            return SchemaUpdateResult(
                success=True,
                message=f"Added enum values for '{table_name}.{column_name}'",
            )
        except Exception as e:
            return SchemaUpdateResult(
                success=False,
                message="",
                error=f"Failed to add enum values: {e}",
            )

    def add_terminology(
        self,
        target: str,
        term: str,
        definition: str,
        sql_pattern: str,
        synonyms: Optional[List[str]] = None,
    ) -> SchemaUpdateResult:
        """Add a terminology entry.

        Args:
            target: Target database name
            term: The business term
            definition: Human-readable definition
            sql_pattern: SQL pattern that implements the term
            synonyms: Optional list of synonyms

        Returns:
            SchemaUpdateResult with success status
        """
        try:
            self._manager.add_terminology(
                target, term, definition, sql_pattern, synonyms
            )
            return SchemaUpdateResult(
                success=True,
                message=f"Added terminology '{term}'",
            )
        except Exception as e:
            return SchemaUpdateResult(
                success=False,
                message="",
                error=f"Failed to add terminology: {e}",
            )

    def add_relationship(
        self,
        target: str,
        source_table: str,
        target_table: str,
        join_pattern: str,
        relationship_type: str = "one_to_many",
    ) -> SchemaUpdateResult:
        """Add a relationship between tables.

        Args:
            target: Target database name
            source_table: Source table name
            target_table: Target table name
            join_pattern: SQL join condition
            relationship_type: Type of relationship

        Returns:
            SchemaUpdateResult with success status
        """
        try:
            self._manager.add_relationship(
                target, source_table, target_table, join_pattern, relationship_type
            )
            return SchemaUpdateResult(
                success=True,
                message=f"Added relationship: {source_table} -> {target_table}",
            )
        except Exception as e:
            return SchemaUpdateResult(
                success=False,
                message="",
                error=f"Failed to add relationship: {e}",
            )

    def add_metric(
        self,
        target: str,
        name: str,
        definition: str,
        sql: str,
        unit: str = "",
    ) -> SchemaUpdateResult:
        """Add or update a metric definition.

        Args:
            target: Target database name
            name: Metric name
            definition: Human-readable definition
            sql: SQL expression for the metric
            unit: Optional unit

        Returns:
            SchemaUpdateResult with success status
        """
        try:
            self._manager.add_metric(target, name, definition, sql, unit)
            return SchemaUpdateResult(
                success=True,
                message=f"Added metric '{name}'",
            )
        except Exception as e:
            return SchemaUpdateResult(
                success=False,
                message="",
                error=f"Failed to add metric: {e}",
            )

    def annotate(
        self,
        target: str,
        target_config: Dict[str, Any],
        table_name: Optional[str] = None,
        sample_rows: int = 5,
    ) -> SchemaUpdateResult:
        """Use LLM to generate descriptions for tables and columns.

        Args:
            target: Target database name
            target_config: Database connection config (for sample data)
            table_name: Optional specific table to annotate (all if None)
            sample_rows: Number of sample rows for context

        Returns:
            SchemaUpdateResult with success status
        """
        # Check for API key first
        if not has_anthropic_api_key():
            return SchemaUpdateResult(
                success=False,
                message="",
                error="Anthropic API key not set (ANTHROPIC_API_KEY or RDST_TRIAL_TOKEN). "
                "Export your API key or run 'rdst init' to configure.",
            )

        if not self._manager.exists(target):
            return SchemaUpdateResult(
                success=False,
                message="",
                error=f"No semantic layer found for '{target}'. Run init first.",
            )

        try:
            from ..semantic_layer.ai_annotator import AIAnnotator

            ai_annotator = AIAnnotator()
        except Exception as e:
            return SchemaUpdateResult(
                success=False,
                message="",
                error=f"LLM not configured: {e}. Run 'rdst configure llm' first.",
            )

        # Create sample data function
        sample_data_fn = None
        if target_config:
            sample_data_fn = self._create_sample_data_function(
                target_config, sample_rows
            )

        try:
            layer = self._manager.load(target)

            # Determine which tables to annotate
            tables_to_annotate = (
                [table_name] if table_name else list(layer.tables.keys())
            )

            # Generate annotations
            ai_annotator.annotate_layer_bulk(layer, tables_to_annotate, sample_data_fn)

            # Save updated layer
            self._manager.save(layer)

            return SchemaUpdateResult(
                success=True,
                message=f"Generated AI annotations for {len(tables_to_annotate)} table(s)",
            )

        except Exception as e:
            return SchemaUpdateResult(
                success=False,
                message="",
                error=f"Annotation failed: {e}",
            )

    def _create_sample_data_function(
        self, target_config: Dict[str, Any], sample_rows: int
    ):
        """Create a function that fetches sample data from a table."""
        import psycopg2

        def get_samples(table_name: str) -> List[Dict]:
            try:
                conn = psycopg2.connect(
                    host=target_config.get("host", "localhost"),
                    port=target_config.get("port", 5432),
                    database=target_config.get("database"),
                    user=target_config.get("user"),
                    password=target_config.get("password"),
                )
                cursor = conn.cursor()
                cursor.execute(f"SELECT * FROM {table_name} LIMIT %s", (sample_rows,))
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
                conn.close()
                return [dict(zip(columns, row)) for row in rows]
            except Exception:
                return []

        return get_samples

    # ========================================================================
    # Event APIs for shared CLI + Web workflows
    # ========================================================================

    async def get_status_events(self, target: str) -> AsyncGenerator[SchemaEvent, None]:
        """Stream status check events."""
        try:
            yield SchemaStatusEvent(
                type="status",
                operation="status",
                message=f"Checking semantic layer status for '{target}'",
            )
            status = self.get_status(target)
            yield SchemaCompleteEvent(
                type="complete",
                operation="status",
                success=True,
                status=status,
            )
        except Exception as e:
            yield SchemaErrorEvent(type="error", operation="status", message=str(e))

    async def get_schema_events(
        self, target: str, table_name: Optional[str] = None
    ) -> AsyncGenerator[SchemaEvent, None]:
        """Stream schema load events."""
        try:
            msg = f"Loading semantic layer for '{target}'"
            if table_name:
                msg += f" table '{table_name}'"
            yield SchemaStatusEvent(type="status", operation="show", message=msg)
            details = self.get_schema(target, table_name)
            if details is None:
                yield SchemaErrorEvent(
                    type="error",
                    operation="show",
                    message=f"No semantic layer found for target '{target}'"
                    + (f" or table '{table_name}' not found" if table_name else ""),
                )
                return

            yield SchemaCompleteEvent(
                type="complete",
                operation="show",
                success=True,
                details=details,
            )
        except Exception as e:
            yield SchemaErrorEvent(type="error", operation="show", message=str(e))

    async def list_targets_events(self) -> AsyncGenerator[SchemaEvent, None]:
        """Stream list target events."""
        try:
            yield SchemaStatusEvent(
                type="status",
                operation="list",
                message="Listing semantic layer targets",
            )
            target_list = self.list_targets()
            yield SchemaCompleteEvent(
                type="complete",
                operation="list",
                success=True,
                target_list=target_list,
            )
        except Exception as e:
            yield SchemaErrorEvent(type="error", operation="list", message=str(e))

    async def init_events(
        self,
        target: str,
        target_config: Dict[str, Any],
        options: Optional[SchemaInitOptions] = None,
    ) -> AsyncGenerator[SchemaEvent, None]:
        """Stream semantic layer initialization events."""
        try:
            yield SchemaStatusEvent(
                type="status",
                operation="init",
                message=f"Initializing semantic layer for '{target}'",
            )
            result = self.init(target, target_config, options)
            if result.success:
                yield SchemaCompleteEvent(
                    type="complete",
                    operation="init",
                    success=True,
                    init_result=result,
                )
            else:
                yield SchemaErrorEvent(
                    type="error",
                    operation="init",
                    message=result.error or "Schema init failed",
                )
        except Exception as e:
            yield SchemaErrorEvent(type="error", operation="init", message=str(e))

    async def export_events(
        self, target: str, format: str = "yaml"
    ) -> AsyncGenerator[SchemaEvent, None]:
        """Stream schema export events."""
        try:
            yield SchemaStatusEvent(
                type="status",
                operation="export",
                message=f"Exporting semantic layer for '{target}' as {format}",
            )
            result = self.export(target, format)
            if result.success:
                yield SchemaCompleteEvent(
                    type="complete",
                    operation="export",
                    success=True,
                    export_result=result,
                )
            else:
                yield SchemaErrorEvent(
                    type="error",
                    operation="export",
                    message=result.error or "Schema export failed",
                )
        except Exception as e:
            yield SchemaErrorEvent(type="error", operation="export", message=str(e))

    async def delete_events(self, target: str) -> AsyncGenerator[SchemaEvent, None]:
        """Stream schema delete events."""
        try:
            yield SchemaStatusEvent(
                type="status",
                operation="delete",
                message=f"Deleting semantic layer for '{target}'",
            )
            result = self.delete(target)
            if result.success:
                yield SchemaCompleteEvent(
                    type="complete",
                    operation="delete",
                    success=True,
                    delete_result=result,
                )
            else:
                yield SchemaErrorEvent(
                    type="error",
                    operation="delete",
                    message=result.error or "Schema delete failed",
                )
        except Exception as e:
            yield SchemaErrorEvent(type="error", operation="delete", message=str(e))
