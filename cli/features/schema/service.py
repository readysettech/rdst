"""Service for semantic layer management."""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Optional

from shared.anthropic_env import has_anthropic_api_key

from .semantic_layer import (
    SchemaIntrospector,
    SemanticLayerManager,
    create_data_profiler,
    parse_row_estimate,
)

from .events import SchemaCompleteEvent, SchemaErrorEvent, SchemaEvent, SchemaStatusEvent
from .models import (
    SchemaCustomType,
    SchemaDeleteResult,
    SchemaDetails,
    SchemaExportResult,
    SchemaExtension,
    SchemaInitOptions,
    SchemaInitResult,
    SchemaMetric,
    SchemaStatus,
    SchemaTable,
    SchemaTableColumn,
    SchemaTableRelationship,
    SchemaTargetList,
    SchemaTargetSummary,
    SchemaTerminology,
    SchemaUpdateResult,
)


class SchemaService:
    """Stateless service for semantic layer management."""

    def __init__(self):
        self._manager = SemanticLayerManager()

    def get_status(self, target: str) -> SchemaStatus:
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
        if not self._manager.exists(target):
            return None

        layer = self._manager.load(target)
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
                    null_fraction=col.null_fraction,
                    distinct_count=col.distinct_count,
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

        terminology = [
            SchemaTerminology(
                term=term,
                definition=t.definition,
                sql_pattern=t.sql_pattern,
                synonyms=t.synonyms or [],
            )
            for term, t in layer.terminology.items()
        ]

        metrics = [
            SchemaMetric(
                name=name,
                definition=m.definition,
                sql=m.sql,
            )
            for name, m in layer.metrics.items()
        ]

        extensions = [
            SchemaExtension(
                name=name,
                version=ext.version or "",
                description=ext.description,
                types_provided=ext.types_provided or [],
            )
            for name, ext in layer.extensions.items()
        ]

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
        target_config: dict[str, Any],
        options: Optional[SchemaInitOptions] = None,
    ) -> SchemaInitResult:
        options = options or SchemaInitOptions()

        if self._manager.exists(target) and not options.force:
            return SchemaInitResult(
                success=False,
                target=target,
                tables=0,
                columns=0,
                relationships=0,
                enum_columns=[],
                error=f"Semantic layer already exists for '{target}'. Use --force to overwrite.",
            )

        try:
            introspector = SchemaIntrospector(target_config)
            layer = introspector.introspect(
                target_name=target,
                enum_threshold=options.enum_threshold,
                sample_enums=options.sample_enums,
            )
            self._manager.save(layer)

            total_columns = sum(len(t.columns) for t in layer.tables.values())
            total_relationships = sum(len(t.relationships) for t in layer.tables.values())

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
        except ConnectionError as exc:
            return SchemaInitResult(
                success=False,
                target=target,
                tables=0,
                columns=0,
                relationships=0,
                enum_columns=[],
                error=f"Database connection failed: {exc}",
            )
        except ValueError as exc:
            return SchemaInitResult(
                success=False,
                target=target,
                tables=0,
                columns=0,
                relationships=0,
                enum_columns=[],
                error=str(exc),
            )
        except Exception as exc:
            return SchemaInitResult(
                success=False,
                target=target,
                tables=0,
                columns=0,
                relationships=0,
                enum_columns=[],
                error=f"Failed to initialize semantic layer: {exc}",
            )

    def refresh(self, target: str, target_config: dict[str, Any]) -> dict[str, Any]:
        """Re-introspect the database and merge structural changes into the
        existing semantic layer, preserving annotations."""
        if not self._manager.exists(target):
            return {
                "ok": False,
                "message": f"No semantic layer found for '{target}'. Run 'rdst schema init --target {target}' first.",
                "data": None,
            }

        try:
            existing = self._manager.load(target)
            introspector = SchemaIntrospector(target_config)
            fresh = introspector.introspect(target_name=target, enum_threshold=20, sample_enums=False)
            changes = {
                "tables_added": [],
                "tables_removed": [],
                "columns_added": [],
                "columns_removed": [],
                "columns_type_changed": [],
                "indexes_added": [],
                "indexes_removed": [],
                "row_estimates_updated": [],
                "relationships_updated": 0,
            }

            all_table_names = set(list(existing.tables.keys()) + list(fresh.tables.keys()))
            for table_name in all_table_names:
                if table_name not in fresh.tables:
                    changes["tables_removed"].append(table_name)
                    del existing.tables[table_name]
                    continue
                if table_name not in existing.tables:
                    changes["tables_added"].append(table_name)
                    existing.tables[table_name] = fresh.tables[table_name]
                    continue

                old_table = existing.tables[table_name]
                new_table = fresh.tables[table_name]

                if old_table.row_estimate != new_table.row_estimate:
                    changes["row_estimates_updated"].append(
                        f"{table_name}: {old_table.row_estimate} -> {new_table.row_estimate}"
                    )
                    old_table.row_estimate = new_table.row_estimate

                old_cols = set(old_table.columns.keys())
                new_cols = set(new_table.columns.keys())
                for col_name in new_cols - old_cols:
                    changes["columns_added"].append(f"{table_name}.{col_name}")
                    old_table.columns[col_name] = new_table.columns[col_name]
                for col_name in old_cols - new_cols:
                    changes["columns_removed"].append(f"{table_name}.{col_name}")
                    del old_table.columns[col_name]
                for col_name in old_cols & new_cols:
                    old_col = old_table.columns[col_name]
                    new_col = new_table.columns[col_name]
                    if old_col.data_type != new_col.data_type and old_col.data_type != "enum":
                        changes["columns_type_changed"].append(
                            f"{table_name}.{col_name}: {old_col.data_type} -> {new_col.data_type}"
                        )
                        old_col.data_type = new_col.data_type
                    old_col.value_pattern = new_col.value_pattern

                old_idx_names = set(old_table.indexes.keys()) if old_table.indexes else set()
                new_idx_names = set(new_table.indexes.keys()) if new_table.indexes else set()
                for idx_name in new_idx_names - old_idx_names:
                    changes["indexes_added"].append(f"{table_name}.{idx_name}")
                for idx_name in old_idx_names - new_idx_names:
                    changes["indexes_removed"].append(f"{table_name}.{idx_name}")

                old_table.indexes = new_table.indexes
                if new_table.relationships:
                    old_table.relationships = new_table.relationships
                    changes["relationships_updated"] += 1

            existing.extensions = fresh.extensions
            existing.custom_types = fresh.custom_types
            self._manager.save(existing)

            total_changes = (
                len(changes["tables_added"])
                + len(changes["tables_removed"])
                + len(changes["columns_added"])
                + len(changes["columns_removed"])
                + len(changes["columns_type_changed"])
                + len(changes["indexes_added"])
                + len(changes["indexes_removed"])
                + len(changes["row_estimates_updated"])
            )
            if total_changes == 0:
                message = f"Schema for '{target}' is up to date. No structural changes detected."
            else:
                message = f"Refreshed schema for '{target}' — {total_changes} change(s), annotations preserved."

            return {
                "ok": True,
                "message": message,
                "data": {
                    "target": target,
                    "changes": {k: v for k, v in changes.items() if v},
                    "total_changes": total_changes,
                    "path": str(self._manager.get_path(target)),
                },
            }
        except ConnectionError as exc:
            return {
                "ok": False,
                "message": f"Database connection failed: {exc}",
                "data": None,
            }
        except Exception as exc:
            return {
                "ok": False,
                "message": f"Failed to refresh schema: {exc}",
                "data": None,
            }

    def profile(
        self,
        target: str,
        target_config: dict[str, Any],
        table_name: Optional[str] = None,
    ) -> dict[str, Any]:
        """Profile column data distributions and store them as annotations."""
        if not self._manager.exists(target):
            return {
                "ok": False,
                "message": f"No semantic layer for '{target}'. Run 'rdst schema init' first.",
            }
        layer = self._manager.load(target)

        profiler = create_data_profiler(target_config)
        if table_name:
            if table_name not in layer.tables:
                return {
                    "ok": False,
                    "message": f"Table '{table_name}' not found in semantic layer.",
                }
            tables_to_profile = {table_name: layer.tables[table_name]}
        else:
            tables_to_profile = layer.tables

        for tname, tann in tables_to_profile.items():
            row_est = parse_row_estimate(tann.row_estimate)
            tp = profiler.profile_table(
                tname,
                tann.columns,
                row_est,
                tann.row_estimate or "0",
                tann.relationships,
            )
            tann.apply_profile(tp)

        self._manager.save(layer)
        return {
            "ok": True,
            "message": f"Profiled {len(tables_to_profile)} table(s) for '{target}'.",
        }

    def export(self, target: str, format: str = "yaml") -> SchemaExportResult:
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

            return SchemaExportResult(success=True, format=format, content=content)
        except Exception as exc:
            return SchemaExportResult(
                success=False,
                format=format,
                content="",
                error=f"Export failed: {exc}",
            )

    def delete(self, target: str) -> SchemaDeleteResult:
        if not self._manager.exists(target):
            return SchemaDeleteResult(
                success=False,
                target=target,
                error=f"No semantic layer found for target '{target}'",
            )

        success = self._manager.delete(target)
        if success:
            return SchemaDeleteResult(success=True, target=target)

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
        try:
            self._manager.add_table(
                target, table_name, description, business_context, row_estimate
            )
            return SchemaUpdateResult(
                success=True,
                message=f"Added table '{table_name}' to semantic layer for '{target}'",
            )
        except Exception as exc:
            return SchemaUpdateResult(
                success=False,
                message="",
                error=f"Failed to add table: {exc}",
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
        except Exception as exc:
            return SchemaUpdateResult(
                success=False,
                message="",
                error=f"Failed to add column: {exc}",
            )

    def add_enum(
        self,
        target: str,
        table_name: str,
        column_name: str,
        enum_values: dict[str, str],
    ) -> SchemaUpdateResult:
        try:
            self._manager.add_enum(target, table_name, column_name, enum_values)
            return SchemaUpdateResult(
                success=True,
                message=f"Added enum values for '{table_name}.{column_name}'",
            )
        except Exception as exc:
            return SchemaUpdateResult(
                success=False,
                message="",
                error=f"Failed to add enum values: {exc}",
            )

    def add_terminology(
        self,
        target: str,
        term: str,
        definition: str,
        sql_pattern: str,
        synonyms: Optional[list[str]] = None,
    ) -> SchemaUpdateResult:
        try:
            self._manager.add_terminology(
                target, term, definition, sql_pattern, synonyms
            )
            return SchemaUpdateResult(success=True, message=f"Added terminology '{term}'")
        except Exception as exc:
            return SchemaUpdateResult(
                success=False,
                message="",
                error=f"Failed to add terminology: {exc}",
            )

    def add_relationship(
        self,
        target: str,
        source_table: str,
        target_table: str,
        join_pattern: str,
        relationship_type: str = "one_to_many",
    ) -> SchemaUpdateResult:
        try:
            self._manager.add_relationship(
                target, source_table, target_table, join_pattern, relationship_type
            )
            return SchemaUpdateResult(
                success=True,
                message=f"Added relationship: {source_table} -> {target_table}",
            )
        except Exception as exc:
            return SchemaUpdateResult(
                success=False,
                message="",
                error=f"Failed to add relationship: {exc}",
            )

    def add_metric(
        self,
        target: str,
        name: str,
        definition: str,
        sql: str,
        unit: str = "",
    ) -> SchemaUpdateResult:
        try:
            self._manager.add_metric(target, name, definition, sql, unit)
            return SchemaUpdateResult(success=True, message=f"Added metric '{name}'")
        except Exception as exc:
            return SchemaUpdateResult(
                success=False,
                message="",
                error=f"Failed to add metric: {exc}",
            )

    def annotate(
        self,
        target: str,
        target_config: dict[str, Any],
        table_name: Optional[str] = None,
        sample_rows: int = 5,
    ) -> SchemaUpdateResult:
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
            from .semantic_layer import create_ai_annotator

            ai_annotator = create_ai_annotator()
        except Exception as exc:
            return SchemaUpdateResult(
                success=False,
                message="",
                error=f"LLM not configured: {exc}. Run 'rdst configure llm' first.",
            )

        sample_data_fn = None
        if target_config:
            sample_data_fn = self._create_sample_data_function(
                target_config, sample_rows
            )

        try:
            layer = self._manager.load(target)
            tables_to_annotate = [table_name] if table_name else list(layer.tables.keys())
            ai_annotator.annotate_layer_bulk(layer, tables_to_annotate, sample_data_fn)
            self._manager.save(layer)
            return SchemaUpdateResult(
                success=True,
                message=f"Generated AI annotations for {len(tables_to_annotate)} table(s)",
            )
        except Exception as exc:
            return SchemaUpdateResult(
                success=False,
                message="",
                error=f"Annotation failed: {exc}",
            )

    def _create_sample_data_function(self, target_config: dict[str, Any], sample_rows: int):
        import psycopg2

        def get_samples(table_name: str) -> list[dict]:
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

    async def get_status_events(self, target: str) -> AsyncGenerator[SchemaEvent, None]:
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
        except Exception as exc:
            yield SchemaErrorEvent(type="error", operation="status", message=str(exc))

    async def get_schema_events(
        self, target: str, table_name: Optional[str] = None
    ) -> AsyncGenerator[SchemaEvent, None]:
        try:
            msg = f"Loading semantic layer for '{target}'"
            if table_name:
                msg += f" table '{table_name}'"
            yield SchemaStatusEvent(type="status", operation="show", message=msg)
            details = self.get_schema(target, table_name)
            if details is None:
                if not self._manager.exists(target):
                    msg = f"No semantic layer found for target '{target}'"
                elif table_name:
                    msg = f"Table '{table_name}' not found in semantic layer for '{target}'"
                else:
                    msg = f"No semantic layer found for target '{target}'"
                yield SchemaErrorEvent(
                    type="error",
                    operation="show",
                    message=msg,
                )
                return

            yield SchemaCompleteEvent(
                type="complete",
                operation="show",
                success=True,
                details=details,
            )
        except Exception as exc:
            yield SchemaErrorEvent(type="error", operation="show", message=str(exc))

    async def list_targets_events(self) -> AsyncGenerator[SchemaEvent, None]:
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
        except Exception as exc:
            yield SchemaErrorEvent(type="error", operation="list", message=str(exc))

    async def init_events(
        self,
        target: str,
        target_config: dict[str, Any],
        options: Optional[SchemaInitOptions] = None,
    ) -> AsyncGenerator[SchemaEvent, None]:
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
        except Exception as exc:
            yield SchemaErrorEvent(type="error", operation="init", message=str(exc))

    async def export_events(
        self, target: str, format: str = "yaml"
    ) -> AsyncGenerator[SchemaEvent, None]:
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
        except Exception as exc:
            yield SchemaErrorEvent(type="error", operation="export", message=str(exc))

    async def delete_events(self, target: str) -> AsyncGenerator[SchemaEvent, None]:
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
        except Exception as exc:
            yield SchemaErrorEvent(type="error", operation="delete", message=str(exc))
