"""
Interactive Annotation Wizard for Semantic Layer

Provides a Rich-based TUI for guided annotation of database schemas.
Users can add descriptions, enum mappings, and terminology interactively.
"""

from typing import Optional, Callable, List, Dict, Any

# Import UI system - handles Rich availability internally
from lib.ui import (
    StyleTokens,
    Layout as UILayout,
    NextSteps,
    get_console,
    Prompt,
    Confirm,
    Rule,
    KeyValueTable,
    Spinner,
    Tree,
    SelectionTable,
    MessagePanel,
    SectionBox,
    StatusLine,
)

from .manager import SemanticLayerManager
from ..data_structures.semantic_layer import (
    SemanticLayer,
    TableAnnotation,
    ColumnAnnotation,
)


class AnnotateWizard:
    """
    Interactive wizard for annotating semantic layers.

    Guides users through adding descriptions to tables, columns,
    enum values, and business terminology.
    """

    def __init__(
        self,
        manager: Optional[SemanticLayerManager] = None,
        ai_annotator=None,
        sample_data_fn: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
        schema_context: Optional[str] = None,
    ):
        """
        Initialize the annotation wizard.

        Args:
            manager: SemanticLayerManager instance. Creates default if None.
            ai_annotator: Optional AIAnnotator instance for AI suggestions
            sample_data_fn: Optional function to get sample data from tables
            schema_context: Optional context about the schema (e.g., "Stack Overflow database")
        """
        self.manager = manager or SemanticLayerManager()
        self.console = get_console()
        self.ai_annotator = ai_annotator
        self.sample_data_fn = sample_data_fn
        self.schema_context = schema_context

    def _show_menu(self, title: str, items: List[tuple]) -> None:
        """
        Display a menu with styled options.

        Args:
            title: Menu title
            items: List of (key, label) tuples, e.g. [("1", "Tables"), ("q", "Quit")]
        """
        table = SelectionTable([label for _, label in items], prompt=title)
        self.console.print(table)

    def run(self, target: str, table_name: Optional[str] = None) -> dict:
        """
        Run the annotation wizard.

        Args:
            target: Target database name
            table_name: Optional specific table to annotate

        Returns:
            Dict with 'ok', 'message', and 'data' keys
        """
        try:
            # Load or create semantic layer
            layer = self.manager.load_or_create(target)

            self.console.print()
            self.console.print(
                SectionBox(
                    title="Semantic Layer Annotation Wizard",
                    content=f"Target: [{StyleTokens.SUCCESS}]{target}[/{StyleTokens.SUCCESS}]",
                    border_style=StyleTokens.PANEL_BORDER,
                )
            )

            if table_name:
                # Annotate specific table
                if table_name not in layer.tables:
                    # Create new table
                    if Confirm.ask(f"Table '{table_name}' not found. Create it?"):
                        layer.tables[table_name] = TableAnnotation(name=table_name)
                    else:
                        return {"ok": False, "message": "Cancelled", "data": None}

                self._annotate_table(layer, table_name)
            else:
                # Show menu
                self._run_main_menu(layer)

            # Save changes
            self.manager.save(layer)

            # Breadcrumb for next steps using NextSteps component
            self.console.print(
                NextSteps(
                    [
                        (
                            f'rdst ask "Show me sample data" --target {target}',
                            "Try natural language queries",
                        ),
                        (
                            f"rdst schema show --target {target}",
                            "View your semantic layer",
                        ),
                    ]
                )
            )

            return {
                "ok": True,
                "message": f"Annotations saved for '{target}'",
                "data": self.manager.get_summary(target),
            }

        except KeyboardInterrupt:
            self.console.print(
                f"\n[{StyleTokens.WARNING}]Cancelled[/{StyleTokens.WARNING}]"
            )
            return {"ok": False, "message": "Cancelled by user", "data": None}
        except Exception as e:
            return {"ok": False, "message": f"Wizard error: {e}", "data": None}

    def _run_main_menu(self, layer: SemanticLayer) -> None:
        """Run the main menu loop."""
        while True:
            self.console.print()
            self._show_summary(layer)

            self.console.print()
            self._show_menu(
                "What would you like to annotate?",
                [
                    ("1", "Tables"),
                    ("2", "Terminology"),
                    ("3", "View current annotations"),
                    ("q", "Save and quit"),
                ],
            )

            choice = Prompt.ask("Choice", choices=["1", "2", "3", "q"], default="q")

            if choice == "1":
                self._tables_menu(layer)
            elif choice == "2":
                self._terminology_menu(layer)
            elif choice == "3":
                self._show_full_annotations(layer)
            else:
                break

    def _show_summary(self, layer: SemanticLayer) -> None:
        """Show summary of current annotations."""
        total_columns = sum(len(t.columns) for t in layer.tables.values())
        total_enums = sum(
            1
            for t in layer.tables.values()
            for c in t.columns.values()
            if c.enum_values
        )

        summary = {
            "Tables": str(len(layer.tables)),
            "Columns": str(total_columns),
            "Enum columns": str(total_enums),
            "Terminology": str(len(layer.terminology)),
            "Metrics": str(len(layer.metrics)),
        }
        self.console.print(KeyValueTable(summary, title="Current Annotations"))

    def _tables_menu(self, layer: SemanticLayer) -> None:
        """Menu for table annotations."""
        while True:
            menu_items = []

            if not layer.tables:
                menu_items.append(
                    (
                        "",
                        f"[{StyleTokens.MUTED}]No tables defined[/{StyleTokens.MUTED}]",
                    )
                )
            else:
                for i, (name, table) in enumerate(layer.tables.items(), 1):
                    desc = (
                        table.description[:40] + "..."
                        if len(table.description) > 40
                        else table.description
                    )
                    desc = (
                        desc
                        or f"[{StyleTokens.MUTED}]no description[/{StyleTokens.MUTED}]"
                    )
                    col_count = len(table.columns)
                    label = f"{name} [{StyleTokens.MUTED}]({col_count} cols)[/{StyleTokens.MUTED}]: {desc}"
                    menu_items.append((str(i), label))

            menu_items.append(("a", "Add new table"))
            menu_items.append(("b", "Back to main menu"))

            self.console.print()
            self._show_menu("Tables", menu_items)

            choices = [str(i) for i in range(1, len(layer.tables) + 1)] + ["a", "b"]
            choice = Prompt.ask("Choice", choices=choices, default="b")

            if choice == "b":
                break
            elif choice == "a":
                self._add_table(layer)
            else:
                # Edit existing table
                table_name = list(layer.tables.keys())[int(choice) - 1]
                self._annotate_table(layer, table_name)

    def _add_table(self, layer: SemanticLayer) -> None:
        """Add a new table annotation."""
        name = Prompt.ask("Table name")
        if not name:
            return

        if name in layer.tables:
            self.console.print(
                f"[{StyleTokens.WARNING}]Table '{name}' already exists[/{StyleTokens.WARNING}]"
            )
            return

        description = Prompt.ask("Description", default="")
        context = Prompt.ask("Business context (when/why used)", default="")

        layer.tables[name] = TableAnnotation(
            name=name, description=description, business_context=context
        )

        self.console.print(
            f"[{StyleTokens.SUCCESS}]✓ Added table '{name}'[/{StyleTokens.SUCCESS}]"
        )

        if Confirm.ask("Add columns to this table?"):
            self._annotate_table(layer, name)

    def _annotate_table(self, layer: SemanticLayer, table_name: str) -> None:
        """Annotate a specific table."""
        table = layer.tables[table_name]

        self.console.print(
            MessagePanel(
                f"Annotating table: {table_name}",
                variant="info",
                title="Table Annotation",
            )
        )

        # Table description
        current = table.description or "[none]"
        self.console.print(StatusLine("Current description", current))

        # Show AI suggestion option if available
        if self.ai_annotator:
            self.console.print(
                f"  [{StyleTokens.SECONDARY}][s][/{StyleTokens.SECONDARY}] - Get AI suggestion"
            )
            prompt_text = f"New description ([{StyleTokens.SECONDARY}]s[/{StyleTokens.SECONDARY}] for AI, Enter to keep)"
        else:
            prompt_text = "New description (Enter to keep)"

        new_desc = Prompt.ask(prompt_text, default="")

        if new_desc.lower() == "s":
            if self.ai_annotator:
                # Generate AI suggestion
                try:
                    with Spinner("Generating AI suggestion..."):
                        sample_data = None
                        if self.sample_data_fn:
                            sample_data = self.sample_data_fn(table_name)

                        suggestion = self.ai_annotator.generate_table_description(
                            table_name=table_name,
                            columns=table.columns,
                            row_estimate=table.row_estimate or "unknown",
                            sample_data=sample_data,
                            schema_context=self.schema_context,
                        )
                    self.console.print(
                        MessagePanel(
                            suggestion,
                            variant="success",
                            title="AI suggestion",
                        )
                    )
                    if Confirm.ask("Use this suggestion?", default=True):
                        table.description = suggestion
                except Exception as e:
                    self.console.print(
                        MessagePanel(
                            f"Error generating suggestion: {e}",
                            variant="error",
                        )
                    )
            else:
                self.console.print(
                    MessagePanel(
                        "AI suggestions not available. Set ANTHROPIC_API_KEY or OPENAI_API_KEY environment variable.",
                        variant="warning",
                    )
                )
        elif new_desc:
            table.description = new_desc

        # Business context
        current = table.business_context or "[none]"
        self.console.print(StatusLine("Current business context", current))
        new_ctx = Prompt.ask("New context (Enter to keep)", default="")
        if new_ctx:
            table.business_context = new_ctx

        # Columns menu
        if Confirm.ask("Annotate columns?", default=True):
            self._columns_menu(table)

    def _columns_menu(self, table: TableAnnotation) -> None:
        """Menu for column annotations."""
        while True:
            menu_items = []

            if not table.columns:
                menu_items.append(
                    (
                        "",
                        f"[{StyleTokens.MUTED}]No columns defined[/{StyleTokens.MUTED}]",
                    )
                )
            else:
                for i, (name, col) in enumerate(table.columns.items(), 1):
                    desc = (
                        col.description[:30] + "..."
                        if len(col.description) > 30
                        else col.description
                    )
                    desc = (
                        desc
                        or f"[{StyleTokens.MUTED}]no description[/{StyleTokens.MUTED}]"
                    )
                    type_info = (
                        f" [{StyleTokens.MUTED}][{col.data_type}][/{StyleTokens.MUTED}]"
                        if col.data_type
                        else ""
                    )
                    enum_info = (
                        f" [{StyleTokens.ACCENT}](enum)[/{StyleTokens.ACCENT}]"
                        if col.enum_values
                        else ""
                    )
                    label = f"{name}{type_info}{enum_info}: {desc}"
                    menu_items.append((str(i), label))

            menu_items.append(("a", "Add new column"))
            menu_items.append(("b", "Back"))

            self.console.print()
            self._show_menu(f"Columns in {table.name}", menu_items)

            choices = [str(i) for i in range(1, len(table.columns) + 1)] + ["a", "b"]
            choice = Prompt.ask("Choice", choices=choices, default="b")

            if choice == "b":
                break
            elif choice == "a":
                self._add_column(table)
            else:
                # Edit existing column
                col_name = list(table.columns.keys())[int(choice) - 1]
                self._annotate_column(table, col_name)

    def _add_column(self, table: TableAnnotation) -> None:
        """Add a new column annotation."""
        name = Prompt.ask("Column name")
        if not name:
            return

        if name in table.columns:
            self.console.print(
                f"[{StyleTokens.WARNING}]Column '{name}' already exists[/{StyleTokens.WARNING}]"
            )
            return

        description = Prompt.ask("Description", default="")
        data_type = Prompt.ask("Data type (string/int/enum/timestamp/etc)", default="")

        col = ColumnAnnotation(name=name, description=description, data_type=data_type)

        # If enum, ask for values
        if data_type.lower() == "enum":
            self._add_enum_values(table, col)

        table.columns[name] = col
        self.console.print(
            f"[{StyleTokens.SUCCESS}]✓ Added column '{name}'[/{StyleTokens.SUCCESS}]"
        )

    def _annotate_column(self, table: TableAnnotation, col_name: str) -> None:
        """Annotate a specific column."""
        col = table.columns[col_name]

        self.console.print(f"\n[bold]Annotating column: {col_name}[/bold]")

        # Description
        current = col.description or "[none]"
        self.console.print(f"Current description: {current}")

        # Show AI suggestion option if available
        if self.ai_annotator:
            self.console.print(
                f"  [{StyleTokens.SECONDARY}][s][/{StyleTokens.SECONDARY}] - Get AI suggestion"
            )
            prompt_text = f"New description ([{StyleTokens.SECONDARY}s][/{StyleTokens.SECONDARY}] for AI, Enter to keep)"
        else:
            prompt_text = "New description (Enter to keep)"

        new_desc = Prompt.ask(prompt_text, default="")

        if new_desc.lower() == "s":
            if self.ai_annotator:
                # Generate AI suggestion
                try:
                    with Spinner("Generating AI suggestion..."):
                        # Get sample values if available
                        sample_values = None
                        if self.sample_data_fn:
                            sample_data = self.sample_data_fn(table.name)
                            if sample_data:
                                sample_values = [
                                    row.get(col_name)
                                    for row in sample_data
                                    if col_name in row
                                ]

                        suggestion = self.ai_annotator.generate_column_description(
                            table_name=table.name,
                            column_name=col_name,
                            data_type=col.data_type or "unknown",
                            sample_values=sample_values,
                            table_context=table.description,
                        )
                    self.console.print(
                        f"[{StyleTokens.SUCCESS}]AI suggestion:[/{StyleTokens.SUCCESS}] {suggestion}"
                    )
                    if Confirm.ask("Use this suggestion?", default=True):
                        col.description = suggestion
                except Exception as e:
                    self.console.print(
                        f"[{StyleTokens.ERROR}]Error generating suggestion: {e}[/{StyleTokens.ERROR}]"
                    )
            else:
                self.console.print(
                    f"[{StyleTokens.WARNING}]AI suggestions not available. Set ANTHROPIC_API_KEY or OPENAI_API_KEY environment variable.[/{StyleTokens.WARNING}]"
                )
        elif new_desc:
            col.description = new_desc

        # Data type
        current = col.data_type or "[none]"
        self.console.print(f"Current type: {current}")
        new_type = Prompt.ask("New type (Enter to keep)", default="")
        if new_type:
            col.data_type = new_type

        # Unit
        if col.data_type in ["int", "decimal", "float", "double"]:
            current = col.unit or "[none]"
            self.console.print(f"Current unit: {current}")
            new_unit = Prompt.ask(
                "Unit (USD, cents, ms, etc - Enter to keep)", default=""
            )
            if new_unit:
                col.unit = new_unit

        # Enum values
        if col.data_type == "enum" or col.enum_values:
            if Confirm.ask("Edit enum values?"):
                self._add_enum_values(table, col)

        # PII flag
        if Confirm.ask(f"Is this PII? (current: {col.is_pii})", default=col.is_pii):
            col.is_pii = True
        else:
            col.is_pii = False

    def _add_enum_values(self, table: TableAnnotation, col: ColumnAnnotation) -> None:
        """Add or edit enum values for a column."""
        self.console.print("\n[bold]Enum Values[/bold]")

        if col.enum_values:
            self.console.print("Current values:")
            self.console.print(
                KeyValueTable(col.enum_values, title="Current Enum Values")
            )

        # Show AI suggestion option if available and column has enum values
        if self.ai_annotator and col.enum_values:
            self.console.print(
                f"  [{StyleTokens.SECONDARY}][s][/{StyleTokens.SECONDARY}] - Get AI suggestions for all values"
            )
            self.console.print(
                f"\nEnter values (format: value=meaning). [{StyleTokens.SECONDARY}s][/{StyleTokens.SECONDARY}] for AI suggestions. Empty line to finish."
            )
        else:
            self.console.print(
                "\nEnter values (format: value=meaning). Empty line to finish."
            )

        while True:
            entry = Prompt.ask("Value", default="")
            if not entry:
                break

            if entry.lower() == "s":
                if self.ai_annotator and col.enum_values:
                    # Generate AI suggestions for all enum values
                    try:
                        with Spinner("Generating AI suggestions for enum values..."):
                            enum_values_list = list(col.enum_values.keys())
                            suggestions = self.ai_annotator.generate_enum_mappings(
                                table_name=table.name,
                                column_name=col.name,
                                enum_values=enum_values_list,
                                schema_context=self.schema_context,
                            )

                        if suggestions:
                            self.console.print(
                                f"[{StyleTokens.SUCCESS}]AI suggestions:[/{StyleTokens.SUCCESS}]"
                            )
                            self.console.print(
                                KeyValueTable(suggestions, title="Enum Suggestions")
                            )

                            if Confirm.ask("Use these suggestions?", default=True):
                                col.enum_values.update(suggestions)
                                self.console.print(
                                    f"[{StyleTokens.SUCCESS}]✓ Updated enum values with AI suggestions[/{StyleTokens.SUCCESS}]"
                                )
                        else:
                            self.console.print(
                                f"[{StyleTokens.WARNING}]No suggestions generated[/{StyleTokens.WARNING}]"
                            )
                    except Exception as e:
                        self.console.print(
                            f"[{StyleTokens.ERROR}]Error generating suggestions: {e}[/{StyleTokens.ERROR}]"
                        )
                    break
                else:
                    self.console.print(
                        f"[{StyleTokens.WARNING}]AI suggestions not available. Use --use-llm flag.[/{StyleTokens.WARNING}]"
                    )
            elif "=" in entry:
                value, meaning = entry.split("=", 1)
                col.enum_values[value.strip()] = meaning.strip()
                self.console.print(
                    f"  [{StyleTokens.SUCCESS}]Added: {value.strip()} = {meaning.strip()}[/{StyleTokens.SUCCESS}]"
                )
            else:
                col.enum_values[entry] = f"TODO: describe '{entry}'"
                self.console.print(
                    f"  [{StyleTokens.WARNING}]Added: {entry} (needs description)[/{StyleTokens.WARNING}]"
                )

    def _terminology_menu(self, layer: SemanticLayer) -> None:
        """Menu for terminology annotations."""
        while True:
            menu_items = []

            if not layer.terminology:
                menu_items.append(
                    (
                        "",
                        f"[{StyleTokens.MUTED}]No terminology defined[/{StyleTokens.MUTED}]",
                    )
                )
            else:
                for i, (term, t) in enumerate(layer.terminology.items(), 1):
                    definition = (
                        t.definition[:40] + "..."
                        if len(t.definition) > 40
                        else t.definition
                    )
                    label = f"[bold]{term}[/bold]: {definition}"
                    menu_items.append((str(i), label))

            menu_items.append(("a", "Add new term"))
            menu_items.append(("b", "Back to main menu"))

            self.console.print()
            self._show_menu("Business Terminology", menu_items)

            choices = [str(i) for i in range(1, len(layer.terminology) + 1)] + [
                "a",
                "b",
            ]
            choice = Prompt.ask("Choice", choices=choices, default="b")

            if choice == "b":
                break
            elif choice == "a":
                self._add_terminology(layer)
            else:
                # Edit existing term
                term = list(layer.terminology.keys())[int(choice) - 1]
                self._edit_terminology(layer, term)

    def _add_terminology(self, layer: SemanticLayer) -> None:
        """Add a new terminology entry."""
        term = Prompt.ask("Term (e.g., 'churned', 'active', 'whale')")
        if not term:
            return

        if term in layer.terminology:
            self.console.print(
                f"[{StyleTokens.WARNING}]Term '{term}' already exists. Use edit instead.[/{StyleTokens.WARNING}]"
            )
            return

        definition = Prompt.ask("Definition (human-readable)")
        sql_pattern = Prompt.ask("SQL pattern (e.g., status = 'A')")

        synonyms_str = Prompt.ask(
            "Synonyms (comma-separated, or Enter for none)", default=""
        )
        synonyms = [s.strip() for s in synonyms_str.split(",")] if synonyms_str else []

        layer.add_terminology(term, definition, sql_pattern, synonyms)
        self.console.print(
            f"[{StyleTokens.SUCCESS}]✓ Added terminology '{term}'[/{StyleTokens.SUCCESS}]"
        )

    def _edit_terminology(self, layer: SemanticLayer, term: str) -> None:
        """Edit an existing terminology entry."""
        t = layer.terminology[term]

        self.console.print(f"\n[bold]Editing term: {term}[/bold]")

        # Definition
        self.console.print(f"Current definition: {t.definition}")
        new_def = Prompt.ask("New definition (Enter to keep)", default="")
        if new_def:
            t.definition = new_def

        # SQL pattern
        self.console.print(f"Current SQL pattern: {t.sql_pattern}")
        new_sql = Prompt.ask("New SQL pattern (Enter to keep)", default="")
        if new_sql:
            t.sql_pattern = new_sql

        # Synonyms
        current_syn = ", ".join(t.synonyms) if t.synonyms else "[none]"
        self.console.print(f"Current synonyms: {current_syn}")
        new_syn = Prompt.ask(
            "New synonyms (comma-separated, Enter to keep)", default=""
        )
        if new_syn:
            t.synonyms = [s.strip() for s in new_syn.split(",")]

    def _show_full_annotations(self, layer: SemanticLayer) -> None:
        """Show all current annotations."""
        self.console.print()
        self.console.print(Rule(f"Semantic Layer: {layer.target}"))

        # Tables
        if layer.tables:
            self.console.print(
                f"\n[{StyleTokens.HEADER}]Tables:[/{StyleTokens.HEADER}]"
            )
            tree = Tree(f"[bold]Tables ({len(layer.tables)})[/bold]")
            for name, table in layer.tables.items():
                table_node = tree.add(
                    f"[{StyleTokens.HEADER}]{name}[/{StyleTokens.HEADER}]"
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

        # Terminology
        if layer.terminology:
            self.console.print(
                f"\n[{StyleTokens.HEADER}]Terminology:[/{StyleTokens.HEADER}]"
            )
            term_tree = Tree(f"[bold]Business Terms ({len(layer.terminology)})[/bold]")
            for term, t in layer.terminology.items():
                term_node = term_tree.add(
                    f"[bold {StyleTokens.SUCCESS}]{term}[/bold {StyleTokens.SUCCESS}]"
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
            self.console.print(
                f"\n[{StyleTokens.HEADER}]Metrics:[/{StyleTokens.HEADER}]"
            )
            metrics_tree = Tree(f"[bold]Metrics ({len(layer.metrics)})[/bold]")
            for name, m in layer.metrics.items():
                metric_node = metrics_tree.add(
                    f"[bold {StyleTokens.ACCENT}]{name}[/bold {StyleTokens.ACCENT}]"
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
        Prompt.ask("Press Enter to continue")
