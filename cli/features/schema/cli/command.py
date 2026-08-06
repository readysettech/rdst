"""Schema command implementation."""

from __future__ import annotations

import subprocess
from typing import Optional

from shared.db_connection import (
    create_mysql_connection_from_params,
    postgres_connection_kwargs,
    quote_identifier,
    resolve_connection_params,
)
from shared.editor import resolve_editor_command
from shared.ui import MessagePanel, Prompt, Rule, SimpleTree, StyleTokens, get_console

from ..semantic_models import SemanticLayer
from ..semantic_layer import (
    AnnotateWizard,
    SchemaIntrospector,
    SemanticLayerManager,
    create_ai_annotator,
    create_guided_annotator,
)


class SchemaCommand:
    """Command handler for `rdst schema` subcommands."""

    def __init__(self, manager: Optional[SemanticLayerManager] = None):
        self.manager = manager or SemanticLayerManager()
        self.console = get_console()

    def show(self, target: str, table_name: Optional[str] = None) -> dict:
        if not self.manager.exists(target):
            return {
                "ok": False,
                "message": f"No semantic layer found for target '{target}'",
                "data": None,
            }

        layer = self.manager.load(target)
        if table_name:
            if table_name not in layer.tables:
                return {
                    "ok": False,
                    "message": f"Table '{table_name}' not found in semantic layer for '{target}'",
                    "data": {"available_tables": list(layer.tables.keys())},
                }
            self._display_annotations(layer, single_table=table_name)
            return {
                "ok": True,
                "message": "",
                "data": {"target": target, "table": table_name},
            }

        self._display_annotations(layer)
        return {"ok": True, "message": "", "data": {"target": target}}

    def _display_annotations(
        self, layer: SemanticLayer, single_table: Optional[str] = None
    ) -> None:
        self.console.print()
        self.console.print(
            Rule(
                f"Semantic Layer: [{StyleTokens.HEADER}]{layer.target}[/{StyleTokens.HEADER}]"
            )
        )

        tables_to_show = (
            {single_table: layer.tables[single_table]} if single_table else layer.tables
        )
        if tables_to_show:
            tree = SimpleTree(f"\n[bold]Tables ({len(tables_to_show)})[/bold]")

            for name, table in tables_to_show.items():
                table_node = tree.add(
                    f"[{StyleTokens.SECONDARY}]{name}[/{StyleTokens.SECONDARY}]"
                )
                if table.description:
                    table_node.add(
                        f"[{StyleTokens.SUCCESS}]Description:[/{StyleTokens.SUCCESS}] {table.description}"
                    )
                if table.business_context:
                    table_node.add(
                        f"[{StyleTokens.WARNING}]Context:[/{StyleTokens.WARNING}] {table.business_context}"
                    )

                if table.columns:
                    columns_node = table_node.add(
                        f"[bold]Columns ({len(table.columns)})[/bold]"
                    )
                    for col_name, col in table.columns.items():
                        type_str = (
                            f" [{StyleTokens.MUTED}]({col.data_type})[/{StyleTokens.MUTED}]"
                            if col.data_type
                            else ""
                        )
                        col_desc = (
                            col.description
                            or f"[{StyleTokens.MUTED}]no description[/{StyleTokens.MUTED}]"
                        )
                        col_node = columns_node.add(
                            f"[{StyleTokens.SECONDARY}]{col_name}[/{StyleTokens.SECONDARY}]{type_str}: {col_desc}"
                        )
                        if col.enum_values:
                            enum_node = col_node.add(
                                f"[{StyleTokens.ACCENT}]Enum values:[/{StyleTokens.ACCENT}]"
                            )
                            for val, meaning in col.enum_values.items():
                                enum_node.add(f"{val} = {meaning}")

            self.console.print(tree)

        if not single_table:
            if layer.extensions:
                self.console.print()
                self.console.print(
                    f"[{StyleTokens.HEADER}]Extensions:[/{StyleTokens.HEADER}]"
                )
                ext_tree = SimpleTree(
                    f"[bold]Installed Extensions ({len(layer.extensions)})[/bold]"
                )
                for name, ext in layer.extensions.items():
                    if ext.description:
                        ext_node = ext_tree.add(
                            f"[{StyleTokens.INFO}]{name}[/{StyleTokens.INFO}] v{ext.version}: {ext.description}"
                        )
                    else:
                        ext_node = ext_tree.add(
                            f"[{StyleTokens.INFO}]{name}[/{StyleTokens.INFO}] v{ext.version}"
                        )
                    if ext.types_provided:
                        ext_node.add(
                            f"[{StyleTokens.MUTED}]Types:[/{StyleTokens.MUTED}] {', '.join(ext.types_provided)}"
                        )
                self.console.print(ext_tree)

            if layer.custom_types:
                self.console.print()
                self.console.print(
                    f"[{StyleTokens.HEADER}]Custom Types:[/{StyleTokens.HEADER}]"
                )
                types_tree = SimpleTree(
                    f"[bold]Custom Types ({len(layer.custom_types)})[/bold]"
                )
                for name, ct in layer.custom_types.items():
                    if ct.type_category == "enum" and ct.enum_values:
                        values_preview = ", ".join(ct.enum_values[:5])
                        if len(ct.enum_values) > 5:
                            values_preview += f"... ({len(ct.enum_values)} total)"
                            types_tree.add(
                                f"[{StyleTokens.ACCENT}]{name}[/{StyleTokens.ACCENT}] [{StyleTokens.MUTED}](enum)[/{StyleTokens.MUTED}]: [{values_preview}]"
                            )
                    elif ct.type_category == "domain" and ct.base_type:
                        types_tree.add(
                            f"[{StyleTokens.ACCENT}]{name}[/{StyleTokens.ACCENT}] [{StyleTokens.MUTED}](domain over {ct.base_type})[/{StyleTokens.MUTED}]"
                        )
                    elif ct.type_category == "base":
                        desc = ct.description or "extension type"
                        types_tree.add(
                            f"[{StyleTokens.ACCENT}]{name}[/{StyleTokens.ACCENT}] [{StyleTokens.MUTED}]({desc})[/{StyleTokens.MUTED}]"
                        )
                    else:
                        types_tree.add(
                            f"[{StyleTokens.ACCENT}]{name}[/{StyleTokens.ACCENT}] [{StyleTokens.MUTED}]({ct.type_category})[/{StyleTokens.MUTED}]"
                        )
                self.console.print(types_tree)

            if layer.terminology:
                self.console.print()
                self.console.print(
                    f"[{StyleTokens.HEADER}]Terminology:[/{StyleTokens.HEADER}]"
                )
                term_tree = SimpleTree(
                    f"[bold]Business Terms ({len(layer.terminology)})[/bold]"
                )
                for term, t in layer.terminology.items():
                    term_node = term_tree.add(
                        f"[{StyleTokens.SUCCESS}]{term}[/{StyleTokens.SUCCESS}]"
                    )
                    term_node.add(
                        f"[{StyleTokens.SUCCESS}]Definition:[/{StyleTokens.SUCCESS}] {t.definition}"
                    )
                    term_node.add(
                        f"[{StyleTokens.SECONDARY}]SQL:[/{StyleTokens.SECONDARY}] {t.sql_pattern}"
                    )
                    if t.synonyms:
                        term_node.add(
                            f"[{StyleTokens.MUTED}]Synonyms:[/{StyleTokens.MUTED}] {', '.join(t.synonyms)}"
                        )
                self.console.print(term_tree)

            if layer.metrics:
                self.console.print()
                self.console.print(
                    f"[{StyleTokens.HEADER}]Metrics:[/{StyleTokens.HEADER}]"
                )
                metrics_tree = SimpleTree(
                    f"[bold]Metrics ({len(layer.metrics)})[/bold]"
                )
                for name, metric in layer.metrics.items():
                    metric_node = metrics_tree.add(
                        f"[{StyleTokens.ACCENT}]{name}[/{StyleTokens.ACCENT}]"
                    )
                    metric_node.add(
                        f"[{StyleTokens.SUCCESS}]Definition:[/{StyleTokens.SUCCESS}] {metric.definition}"
                    )
                    metric_node.add(
                        f"[{StyleTokens.SECONDARY}]SQL:[/{StyleTokens.SECONDARY}] {metric.sql}"
                    )
                self.console.print(metrics_tree)

        self.console.print()
        self.console.print(Rule())

    def list_targets(self) -> dict:
        targets = self.manager.list_targets()
        if not targets:
            return {
                "ok": True,
                "message": "No semantic layers found",
                "data": {"targets": []},
            }

        target_info = []
        for target in targets:
            summary = self.manager.get_summary(target)
            target_info.append(
                {
                    "name": target,
                    "tables": summary["tables"],
                    "terminology": summary["terminology"],
                    "updated_at": summary.get("updated_at", "unknown"),
                }
            )

        return {
            "ok": True,
            "message": f"Found {len(targets)} semantic layer(s)",
            "data": {"targets": target_info},
        }

    def edit(self, target: str, table_name: Optional[str] = None) -> dict:
        if not self.manager.exists(target):
            return {
                "ok": False,
                "message": f"No semantic layer found for target '{target}'",
                "data": None,
            }

        layer = self.manager.load(target)
        path = self.manager.get_path(target)

        editor_command = resolve_editor_command()
        if editor_command is None:
            return {
                "ok": False,
                "message": "No editor found. Set EDITOR or VISUAL.",
                "data": None,
            }
        try:
            subprocess.run([*editor_command, str(path)], check=True)
            try:
                self.manager.clear_cache()
                self.manager.load(target)
                return {
                    "ok": True,
                    "message": f"Semantic layer updated for '{target}'",
                    "data": {
                        "path": str(path),
                        "summary": self.manager.get_summary(target),
                    },
                }
            except Exception as exc:
                return {
                    "ok": False,
                    "message": f"Error loading updated semantic layer: {exc}",
                    "data": {"path": str(path)},
                }
        except subprocess.CalledProcessError as exc:
            return {
                "ok": False,
                "message": f"Editor exited with error: {exc}",
                "data": {"path": str(path)},
            }
        except OSError as exc:
            return {
                "ok": False,
                "message": f"Could not run editor: {exc}",
                "data": {"editor": editor_command[0]},
            }

    def export(self, target: str, output_format: str = "yaml") -> dict:
        if not self.manager.exists(target):
            return {
                "ok": False,
                "message": f"No semantic layer found for target '{target}'",
                "data": None,
            }

        try:
            if output_format == "yaml":
                content = self.manager.export_yaml(target)
            elif output_format == "json":
                import json

                layer = self.manager.load(target)
                content = json.dumps(layer.to_dict(), indent=2, default=str)
            else:
                return {
                    "ok": False,
                    "message": f"Unknown format: {output_format}. Use 'yaml' or 'json'.",
                    "data": None,
                }
            return {
                "ok": True,
                "message": f"Exported semantic layer for '{target}'",
                "data": {"format": output_format, "content": content},
            }
        except Exception as exc:
            return {"ok": False, "message": f"Export failed: {exc}", "data": None}

    def delete(self, target: str) -> dict:
        if not self.manager.exists(target):
            return {
                "ok": False,
                "message": f"No semantic layer found for target '{target}'",
                "data": None,
            }

        path = self.manager.get_path(target)
        success = self.manager.delete(target)
        if success:
            return {
                "ok": True,
                "message": f"Deleted semantic layer for '{target}'",
                "data": {"path": str(path)},
            }
        return {
            "ok": False,
            "message": f"Failed to delete semantic layer for '{target}'",
            "data": None,
        }

    def add_table(
        self,
        target: str,
        table_name: str,
        description: str,
        business_context: str = "",
        row_estimate: str = "",
    ) -> dict:
        try:
            self.manager.add_table(
                target, table_name, description, business_context, row_estimate
            )
            return {
                "ok": True,
                "message": f"Added table '{table_name}' to semantic layer for '{target}'",
                "data": {"table": table_name, "description": description},
            }
        except Exception as exc:
            return {"ok": False, "message": f"Failed to add table: {exc}", "data": None}

    def add_column(
        self, target: str, table_name: str, column_name: str, description: str, **kwargs
    ) -> dict:
        try:
            self.manager.add_column(
                target, table_name, column_name, description, **kwargs
            )
            return {
                "ok": True,
                "message": f"Added column '{table_name}.{column_name}'",
                "data": {
                    "table": table_name,
                    "column": column_name,
                    "description": description,
                },
            }
        except Exception as exc:
            return {"ok": False, "message": f"Failed to add column: {exc}", "data": None}

    def add_enum(
        self, target: str, table_name: str, column_name: str, enum_values: dict
    ) -> dict:
        try:
            self.manager.add_enum(target, table_name, column_name, enum_values)
            return {
                "ok": True,
                "message": f"Added enum values for '{table_name}.{column_name}'",
                "data": {
                    "table": table_name,
                    "column": column_name,
                    "values": list(enum_values.keys()),
                },
            }
        except Exception as exc:
            return {
                "ok": False,
                "message": f"Failed to add enum values: {exc}",
                "data": None,
            }

    def add_terminology(
        self,
        target: str,
        term: str,
        definition: str,
        sql_pattern: str,
        synonyms: list | None = None,
    ) -> dict:
        try:
            self.manager.add_terminology(
                target, term, definition, sql_pattern, synonyms
            )
            return {
                "ok": True,
                "message": f"Added terminology '{term}'",
                "data": {"term": term, "definition": definition},
            }
        except Exception as exc:
            return {
                "ok": False,
                "message": f"Failed to add terminology: {exc}",
                "data": None,
            }

    def add_relationship(
        self,
        target: str,
        source_table: str,
        target_table: str,
        join_pattern: str,
        relationship_type: str = "one_to_many",
    ) -> dict:
        try:
            self.manager.add_relationship(
                target, source_table, target_table, join_pattern, relationship_type
            )
            return {
                "ok": True,
                "message": f"Added relationship: {source_table} -> {target_table}",
                "data": {
                    "source": source_table,
                    "target": target_table,
                    "type": relationship_type,
                },
            }
        except Exception as exc:
            return {
                "ok": False,
                "message": f"Failed to add relationship: {exc}",
                "data": None,
            }

    def _annotate_enums_interactive(self, layer) -> None:
        enum_columns = []
        for table_name, table in layer.tables.items():
            for col_name, col in table.columns.items():
                if col.enum_values:
                    enum_columns.append((table_name, col_name, col))

        if not enum_columns:
            return

        self.console.print(
            f"\nFound {len(enum_columns)} enum column(s) requiring annotation.\n"
        )

        skip_all = False
        for idx, (table_name, col_name, col) in enumerate(enum_columns, 1):
            if skip_all:
                break

            self.console.print(
                f"[{idx}/{len(enum_columns)}] {table_name}.{col_name} ({len(col.enum_values)} values)"
            )

            try:
                choice = Prompt.ask(
                    "  Annotate? [y/n/skip-all/q]", default="n", show_default=False
                )
            except (EOFError, KeyboardInterrupt):
                self.console.print(
                    MessagePanel("Skipping remaining enums.", variant="warning")
                )
                break

            if choice in ["q", "quit"]:
                self.console.print(
                    MessagePanel("Quitting annotation.", variant="warning")
                )
                break
            if choice in ["s", "skip-all"]:
                skip_all = True
                self.console.print(
                    MessagePanel("Skipping all remaining enums.", variant="warning")
                )
                break
            if choice not in ["y", "yes", ""]:
                self.console.print(MessagePanel("Skipped.", variant="info"))
                continue

            new_enum_values = {}
            for value in sorted(
                col.enum_values.keys(), key=lambda x: (isinstance(x, str), x)
            ):
                try:
                    desc = Prompt.ask(f"    {value} =", default="", show_default=False)
                    if desc:
                        new_enum_values[value] = desc
                    else:
                        new_enum_values[value] = col.enum_values[value]
                except (EOFError, KeyboardInterrupt):
                    for remaining in col.enum_values:
                        if remaining not in new_enum_values:
                            new_enum_values[remaining] = col.enum_values[remaining]
                    break

            col.enum_values = new_enum_values
            self.console.print()

    def init(
        self,
        target: str,
        target_config: dict,
        enum_threshold: int = 20,
        force: bool = False,
        interactive: bool = False,
    ) -> dict:
        if self.manager.exists(target) and not force:
            return {
                "ok": False,
                "message": f"Semantic layer already exists for '{target}'. Use --force to overwrite.",
                "data": {"existing": self.manager.get_summary(target)},
            }

        try:
            introspector = SchemaIntrospector(target_config)
            layer = introspector.introspect(
                target_name=target, enum_threshold=enum_threshold, sample_enums=True
            )
            if interactive:
                self._annotate_enums_interactive(layer)
            self.manager.save(layer)

            total_columns = sum(len(t.columns) for t in layer.tables.values())
            total_relationships = sum(len(t.relationships) for t in layer.tables.values())
            enum_columns = []
            for table_name, table in layer.tables.items():
                for col_name, col in table.columns.items():
                    if col.enum_values:
                        enum_columns.append(f"{table_name}.{col_name}")

            return {
                "ok": True,
                "message": f"Initialized semantic layer for '{target}'",
                "data": {
                    "target": target,
                    "tables": len(layer.tables),
                    "columns": total_columns,
                    "relationships": total_relationships,
                    "enum_columns": enum_columns,
                    "path": str(self.manager.get_path(target)),
                    "next_steps": [
                        f"  rdst schema annotate --target {target} --use-llm   AI-generate descriptions",
                        f"  rdst schema edit --target {target}                 Manual editing in $EDITOR",
                        f'  rdst ask "How many rows in each table?" --target {target}   Try natural language queries',
                    ],
                },
            }
        except ConnectionError as exc:
            return {
                "ok": False,
                "message": f"Database connection failed: {exc}",
                "data": None,
            }
        except ValueError as exc:
            return {"ok": False, "message": str(exc), "data": None}
        except Exception as exc:
            return {
                "ok": False,
                "message": f"Failed to initialize semantic layer: {exc}",
                "data": None,
            }

    def refresh(self, target: str, target_config: dict) -> dict:
        from ..service import SchemaService

        return SchemaService().refresh(target, target_config)

    def profile(
        self,
        target: str,
        target_config: dict,
        table_name: str | None = None,
    ) -> dict:
        from ..service import SchemaService

        return SchemaService().profile(target, target_config, table_name)

    def annotate(
        self,
        target: str,
        table_name: Optional[str] = None,
        use_llm: bool = False,
        auto_accept: bool = False,
        sample_rows: int = 5,
        target_config: Optional[dict] = None,
    ) -> dict:
        if use_llm:
            if not target_config:
                return {
                    "ok": False,
                    "message": f"Target '{target}' not configured. Run 'rdst configure' first.",
                }

            layer = self.manager.load_or_create(target)
            if not layer.tables:
                return {
                    "ok": False,
                    "message": f"No schema found for '{target}'. Run 'rdst schema init --target {target}' first.",
                }

            try:
                annotator = create_guided_annotator(
                    console=self.console, manager=self.manager
                )
                annotator.run(layer, target_config, table_name, auto_accept=auto_accept)
                return {"ok": True, "message": "Guided annotation complete."}
            except Exception as exc:
                return {"ok": False, "message": f"Guided annotation failed: {exc}"}

        ai_annotator = None
        try:
            ai_annotator = create_ai_annotator()
        except Exception:
            pass

        sample_data_fn = None
        if target_config:
            try:
                sample_data_fn = self._create_sample_data_function(
                    target, target_config, sample_rows
                )
            except Exception:
                pass

        wizard = AnnotateWizard(
            manager=self.manager,
            ai_annotator=ai_annotator,
            sample_data_fn=sample_data_fn,
            schema_context=f"{target} database",
        )
        return wizard.run(target, table_name)

    def _create_sample_data_function(
        self, target: str, target_config: dict, sample_rows: int
    ):
        def sample_data(table_name: str) -> list[dict]:
            params = resolve_connection_params(
                target=target,
                target_config=target_config,
            )
            engine = params["engine"]

            if engine in ("postgresql", "postgres"):
                import psycopg2
                import psycopg2.extras

                conn = psycopg2.connect(
                    **postgres_connection_kwargs(params, connect_timeout=5)
                )
                try:
                    with conn.cursor(
                        cursor_factory=psycopg2.extras.RealDictCursor
                    ) as cursor:
                        cursor.execute(
                            f"""
                            SELECT * FROM {quote_identifier(table_name)}
                            TABLESAMPLE SYSTEM(1)
                            LIMIT {int(sample_rows)}
                        """
                        )
                        return [dict(row) for row in cursor.fetchall()]
                finally:
                    conn.close()

            if engine == "mysql":
                import pymysql
                import pymysql.cursors

                conn = create_mysql_connection_from_params(
                    params,
                    connect_timeout=5,
                    cursorclass=pymysql.cursors.DictCursor,
                )
                try:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            f"SELECT * FROM {quote_identifier(table_name, 'mysql')} "
                            f"LIMIT {int(sample_rows)}"
                        )
                        return cursor.fetchall()
                finally:
                    conn.close()

            return []

        return sample_data
