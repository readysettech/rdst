"""
Schema Command - Semantic Layer Management

Provides CLI commands for managing the semantic layer:
- rdst schema show [table] - Display semantic layer
- rdst schema annotate <table> - Interactive annotation wizard
- rdst schema edit [table] - Edit in $EDITOR
- rdst schema init - Bootstrap from database schema
- rdst schema export - Export as YAML
"""

from typing import Optional, List, Dict
import subprocess
import os

from ..semantic_layer.manager import SemanticLayerManager
from ..semantic_layer.introspector import SchemaIntrospector
from ..semantic_layer.annotate_wizard import AnnotateWizard
from ..data_structures.semantic_layer import SemanticLayer

# Import UI system - handles Rich availability internally
from lib.ui import MessagePanel, Prompt, Rule, SimpleTree, StyleTokens, get_console


class SchemaCommand:
    """
    Command handler for rdst schema subcommands.

    Manages the semantic layer which stores domain knowledge about
    database schemas to improve NL-to-SQL generation.
    """

    def __init__(self, manager: Optional[SemanticLayerManager] = None):
        """
        Initialize the schema command.

        Args:
            manager: SemanticLayerManager instance. If None, creates default.
        """
        self.manager = manager or SemanticLayerManager()
        self.console = get_console()

    def show(self, target: str, table_name: Optional[str] = None) -> dict:
        """
        Display semantic layer information using Rich formatting.

        Uses the same display format as 'rdst schema annotate -> View current annotations'.

        Args:
            target: Target database name
            table_name: Optional specific table to show

        Returns:
            Dict with 'ok', 'message', and 'data' keys
        """
        if not self.manager.exists(target):
            return {
                "ok": False,
                "message": f"No semantic layer found for target '{target}'",
                "data": None,
            }

        layer = self.manager.load(target)

        if table_name:
            # Show specific table
            if table_name not in layer.tables:
                return {
                    "ok": False,
                    "message": f"Table '{table_name}' not found in semantic layer for '{target}'",
                    "data": {"available_tables": list(layer.tables.keys())},
                }

            # Display single table
            self._display_annotations(layer, single_table=table_name)
            return {
                "ok": True,
                "message": "",  # Already displayed
                "data": {"target": target, "table": table_name},
            }

        # Display full semantic layer
        self._display_annotations(layer)
        return {
            "ok": True,
            "message": "",  # Already displayed via Rich
            "data": {"target": target},
        }

    def _display_annotations(
        self, layer: SemanticLayer, single_table: Optional[str] = None
    ) -> None:
        """
        Display semantic layer annotations using tree format.

        Uses UI components that handle Rich/plain text automatically.
        """
        self.console.print()
        self.console.print(
            Rule(
                f"Semantic Layer: [{StyleTokens.HEADER}]{layer.target}[/{StyleTokens.HEADER}]"
            )
        )

        # Filter tables if showing single table
        tables_to_show = (
            {single_table: layer.tables[single_table]} if single_table else layer.tables
        )

        # Tables
        if tables_to_show:
            self.console.print(
                f"\n[{StyleTokens.HEADER}]Tables:[/{StyleTokens.HEADER}]"
            )
            tree = SimpleTree(f"[bold]Tables ({len(tables_to_show)})[/bold]")

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

        # Only show extensions, custom_types, terminology and metrics for full display (not single table)
        if not single_table:
            # Extensions
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

            # Custom Types
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

            # Terminology
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

            # Metrics
            if layer.metrics:
                self.console.print()
                self.console.print(
                    f"[{StyleTokens.HEADER}]Metrics:[/{StyleTokens.HEADER}]"
                )
                metrics_tree = SimpleTree(
                    f"[bold]Metrics ({len(layer.metrics)})[/bold]"
                )
                for name, m in layer.metrics.items():
                    metric_node = metrics_tree.add(
                        f"[{StyleTokens.ACCENT}]{name}[/{StyleTokens.ACCENT}]"
                    )
                    metric_node.add(
                        f"[{StyleTokens.SUCCESS}]Definition:[/{StyleTokens.SUCCESS}] {m.definition}"
                    )
                    metric_node.add(
                        f"[{StyleTokens.SECONDARY}]SQL:[/{StyleTokens.SECONDARY}] {m.sql}"
                    )
                self.console.print(metrics_tree)

        self.console.print()
        self.console.print(Rule())

    def _format_table(self, name: str, table) -> dict:
        """Format a TableAnnotation for display."""
        result = {
            "name": name,
            "description": table.description,
        }

        if table.business_context:
            result["business_context"] = table.business_context
        if table.row_estimate:
            result["row_estimate"] = table.row_estimate
        if table.data_freshness:
            result["data_freshness"] = table.data_freshness

        if table.columns:
            result["columns"] = {}
            for col_name, col in table.columns.items():
                col_info = {"description": col.description}
                if col.data_type:
                    col_info["type"] = col.data_type
                if col.unit:
                    col_info["unit"] = col.unit
                if col.enum_values:
                    col_info["enum_values"] = col.enum_values
                if col.is_pii:
                    col_info["is_pii"] = True
                result["columns"][col_name] = col_info

        if table.relationships:
            result["relationships"] = [
                {
                    "target": rel.target_table,
                    "type": rel.relationship_type,
                    "join": rel.join_pattern,
                }
                for rel in table.relationships
            ]

        if table.access_hints:
            result["access_hints"] = table.access_hints

        return result

    def list_targets(self) -> dict:
        """
        List all targets with semantic layers.

        Returns:
            Dict with list of targets and their summaries
        """
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
        """
        Open semantic layer in $EDITOR for editing.

        Args:
            target: Target database name
            table_name: Optional table to focus on (scrolls to that section)

        Returns:
            Dict with result of edit operation
        """
        # Get or create the layer
        layer = self.manager.load_or_create(target)

        # Get the file path
        path = self.manager.get_path(target)

        # Ensure file exists on disk
        if not path.exists():
            self.manager.save(layer)

        # Get editor from environment
        editor = os.environ.get("EDITOR", os.environ.get("VISUAL", "vim"))

        try:
            # Open in editor
            result = subprocess.run([editor, str(path)], check=True)

            # Reload and validate
            try:
                self.manager.clear_cache()
                updated_layer = self.manager.load(target)

                return {
                    "ok": True,
                    "message": f"Semantic layer updated for '{target}'",
                    "data": {
                        "path": str(path),
                        "summary": self.manager.get_summary(target),
                    },
                }
            except Exception as e:
                return {
                    "ok": False,
                    "message": f"Error loading updated semantic layer: {e}",
                    "data": {"path": str(path)},
                }

        except subprocess.CalledProcessError as e:
            return {
                "ok": False,
                "message": f"Editor exited with error: {e}",
                "data": {"path": str(path)},
            }
        except FileNotFoundError:
            return {
                "ok": False,
                "message": f"Editor not found: {editor}. Set $EDITOR environment variable.",
                "data": {"editor": editor},
            }

    def export(self, target: str, output_format: str = "yaml") -> dict:
        """
        Export semantic layer to string.

        Args:
            target: Target database name
            output_format: Format to export ('yaml' or 'json')

        Returns:
            Dict with exported content
        """
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
        except Exception as e:
            return {"ok": False, "message": f"Export failed: {e}", "data": None}

    def delete(self, target: str) -> dict:
        """
        Delete semantic layer for a target.

        Args:
            target: Target database name

        Returns:
            Dict with result of delete operation
        """
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
        else:
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
        """
        Add or update a table annotation.

        Args:
            target: Target database name
            table_name: Name of the table
            description: Table description
            business_context: Optional business context
            row_estimate: Optional row count estimate

        Returns:
            Dict with result
        """
        try:
            self.manager.add_table(
                target, table_name, description, business_context, row_estimate
            )
            return {
                "ok": True,
                "message": f"Added table '{table_name}' to semantic layer for '{target}'",
                "data": {"table": table_name, "description": description},
            }
        except Exception as e:
            return {"ok": False, "message": f"Failed to add table: {e}", "data": None}

    def add_column(
        self, target: str, table_name: str, column_name: str, description: str, **kwargs
    ) -> dict:
        """
        Add or update a column annotation.

        Args:
            target: Target database name
            table_name: Name of the table
            column_name: Name of the column
            description: Column description
            **kwargs: Additional column properties (data_type, unit, etc.)

        Returns:
            Dict with result
        """
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
        except Exception as e:
            return {"ok": False, "message": f"Failed to add column: {e}", "data": None}

    def add_enum(
        self, target: str, table_name: str, column_name: str, enum_values: dict
    ) -> dict:
        """
        Add enum value mappings for a column.

        Args:
            target: Target database name
            table_name: Name of the table
            column_name: Name of the column
            enum_values: Dict of value -> meaning

        Returns:
            Dict with result
        """
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
        except Exception as e:
            return {
                "ok": False,
                "message": f"Failed to add enum values: {e}",
                "data": None,
            }

    def add_terminology(
        self,
        target: str,
        term: str,
        definition: str,
        sql_pattern: str,
        synonyms: list = None,
    ) -> dict:
        """
        Add a terminology entry.

        Args:
            target: Target database name
            term: The business term
            definition: Human-readable definition
            sql_pattern: SQL pattern that implements the term
            synonyms: Optional list of synonyms

        Returns:
            Dict with result
        """
        try:
            self.manager.add_terminology(
                target, term, definition, sql_pattern, synonyms
            )
            return {
                "ok": True,
                "message": f"Added terminology '{term}'",
                "data": {"term": term, "definition": definition},
            }
        except Exception as e:
            return {
                "ok": False,
                "message": f"Failed to add terminology: {e}",
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
        """
        Add a relationship between tables.

        Args:
            target: Target database name
            source_table: Source table name
            target_table: Target table name
            join_pattern: SQL join condition
            relationship_type: Type of relationship

        Returns:
            Dict with result
        """
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
        except Exception as e:
            return {
                "ok": False,
                "message": f"Failed to add relationship: {e}",
                "data": None,
            }

    def _annotate_enums_interactive(self, layer) -> None:
        """
        Interactively prompt user for enum value descriptions.

        Args:
            layer: SemanticLayer to annotate
        """
        # Collect all enum columns
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

            # Ask if user wants to annotate this column
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
            elif choice in ["s", "skip-all"]:
                skip_all = True
                self.console.print(
                    MessagePanel("Skipping all remaining enums.", variant="warning")
                )
                break
            elif choice not in ["y", "yes", ""]:
                self.console.print(MessagePanel("Skipped.", variant="info"))
                continue

            # Annotate each value
            new_enum_values = {}
            for value in sorted(
                col.enum_values.keys(), key=lambda x: (isinstance(x, str), x)
            ):
                try:
                    desc = Prompt.ask(f"    {value} =", default="", show_default=False)
                    if desc:
                        new_enum_values[value] = desc
                    else:
                        new_enum_values[value] = col.enum_values[value]  # Keep TODO
                except (EOFError, KeyboardInterrupt):
                    # Keep remaining values as-is
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
        """
        Initialize semantic layer by introspecting database schema.

        Args:
            target: Target name for the semantic layer
            target_config: Database configuration dict
            enum_threshold: Max distinct values to consider as enum
            force: Overwrite existing semantic layer if present
            interactive: Prompt user for enum value descriptions

        Returns:
            Dict with result including summary of what was discovered
        """
        # Check if already exists
        if self.manager.exists(target) and not force:
            return {
                "ok": False,
                "message": f"Semantic layer already exists for '{target}'. Use --force to overwrite.",
                "data": {"existing": self.manager.get_summary(target)},
            }

        try:
            # Introspect database
            introspector = SchemaIntrospector(target_config)
            layer = introspector.introspect(
                target_name=target, enum_threshold=enum_threshold, sample_enums=True
            )

            # Interactive enum annotation
            if interactive:
                self._annotate_enums_interactive(layer)

            # Save the layer
            self.manager.save(layer)

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

        except ConnectionError as e:
            return {
                "ok": False,
                "message": f"Database connection failed: {e}",
                "data": None,
            }
        except ValueError as e:
            return {"ok": False, "message": str(e), "data": None}
        except Exception as e:
            return {
                "ok": False,
                "message": f"Failed to initialize semantic layer: {e}",
                "data": None,
            }

    def annotate(
        self,
        target: str,
        table_name: Optional[str] = None,
        use_llm: bool = False,
        sample_rows: int = 5,
        target_config: Optional[dict] = None,
    ) -> dict:
        """
        Run interactive annotation wizard.

        Args:
            target: Target database name
            table_name: Optional specific table to annotate
            use_llm: Use LLM to generate suggestions before wizard
            sample_rows: Number of sample rows to use for context
            target_config: Database config (required if use_llm=True)

        Returns:
            Dict with result from wizard
        """
        # Always try to enable AI support in wizard (if API key available)
        ai_annotator = None
        try:
            from ..semantic_layer.ai_annotator import AIAnnotator

            ai_annotator = AIAnnotator()  # Will raise if no API key
        except Exception:
            # Silently skip - AI suggestions will be unavailable in wizard
            pass

        # Always try to create sample data function (if target configured)
        sample_data_fn = None
        if target_config:
            try:
                sample_data_fn = self._create_sample_data_function(
                    target_config, sample_rows
                )
            except Exception:
                # Database connection issues - sample data unavailable
                pass

        # Always set schema context
        schema_context = f"{target} database"

        # Run bulk pre-population only if --use-llm flag is set
        if use_llm:
            self.console.print("Generating AI-powered annotation suggestions...")
            self.console.print(
                f"[{StyleTokens.MUTED}](This may take a minute for large schemas)[/{StyleTokens.MUTED}]\n"
            )

            # Check prerequisites
            if not ai_annotator:
                return {
                    "ok": False,
                    "message": "No LLM API key configured. Run 'rdst configure llm' to set up AI support.",
                }

            if not target_config:
                return {
                    "ok": False,
                    "message": f"Target '{target}' not configured. Run 'rdst configure' first.",
                }

            # Load existing semantic layer
            if not self.manager.exists(target):
                return {
                    "ok": False,
                    "message": f"No semantic layer found for '{target}'. Run 'rdst schema init' first.",
                }

            layer = self.manager.load(target)

            # Determine which tables to annotate
            tables_to_annotate = (
                [table_name] if table_name else list(layer.tables.keys())
            )

            # Generate annotations
            try:
                ai_annotator.annotate_layer_bulk(
                    layer, tables_to_annotate, sample_data_fn
                )

                # Save pre-populated layer
                self.manager.save(layer)

                self.console.print(
                    f"[{StyleTokens.SUCCESS}]Generated suggestions for {len(tables_to_annotate)} table(s)[/{StyleTokens.SUCCESS}]"
                )
                self.console.print(
                    "  Review and edit suggestions in the wizard below.\n"
                )

            except Exception as e:
                self.console.print(
                    f"[{StyleTokens.WARNING}]LLM annotation failed: {e}[/{StyleTokens.WARNING}]"
                )
                self.console.print("Continuing with manual annotation...\n")

        # Run interactive wizard with AI support (if enabled)
        wizard = AnnotateWizard(
            manager=self.manager,
            ai_annotator=ai_annotator,
            sample_data_fn=sample_data_fn,
            schema_context=schema_context,
        )
        return wizard.run(target, table_name)

    def _create_sample_data_function(self, target_config: dict, sample_rows: int):
        """
        Create a function that samples data from the database.

        Args:
            target_config: Database configuration
            sample_rows: Number of rows to sample

        Returns:
            Function(table_name) -> List[Dict] that returns sample data
        """

        def sample_data(table_name: str) -> List[Dict]:
            """Sample N rows from a table."""
            engine = target_config.get("engine", "").lower()

            if engine in ["postgresql", "postgres"]:
                import psycopg2
                import psycopg2.extras

                # Get connection params
                password_env = target_config.get("password_env")
                password = os.environ.get(password_env) if password_env else None

                conn = psycopg2.connect(
                    host=target_config.get("host"),
                    port=target_config.get("port", 5432),
                    user=target_config.get("user"),
                    password=password,
                    database=target_config.get("database"),
                    connect_timeout=5,
                )

                try:
                    with conn.cursor(
                        cursor_factory=psycopg2.extras.RealDictCursor
                    ) as cursor:
                        # Use TABLESAMPLE for large tables
                        cursor.execute(f"""
                            SELECT * FROM "{table_name}"
                            TABLESAMPLE SYSTEM(1)
                            LIMIT {sample_rows}
                        """)
                        return [dict(row) for row in cursor.fetchall()]
                finally:
                    conn.close()

            elif engine == "mysql":
                import pymysql
                import pymysql.cursors

                password_env = target_config.get("password_env")
                password = os.environ.get(password_env) if password_env else None

                conn = pymysql.connect(
                    host=target_config.get("host"),
                    port=target_config.get("port", 3306),
                    user=target_config.get("user"),
                    password=password,
                    database=target_config.get("database"),
                    cursorclass=pymysql.cursors.DictCursor,
                    connect_timeout=5,
                )

                try:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            f"SELECT * FROM `{table_name}` LIMIT {sample_rows}"
                        )
                        return cursor.fetchall()
                finally:
                    conn.close()

            return []

        return sample_data
