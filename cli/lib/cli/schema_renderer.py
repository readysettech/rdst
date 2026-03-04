"""Renderer for schema service events."""

from __future__ import annotations

from lib.services.types import (
    SchemaCompleteEvent,
    SchemaDetails,
    SchemaErrorEvent,
    SchemaEvent,
    SchemaStatusEvent,
)
from lib.ui import (
    KeyValueTable,
    MessagePanel,
    NextSteps,
    Rule,
    SimpleTree,
    StyleTokens,
    get_console,
)


class SchemaRenderer:
    """Render schema events while keeping CLI semantics stable."""

    def __init__(self):
        self._console = get_console()
        self._single_table_show = False

    def render(self, event: SchemaEvent) -> None:
        if isinstance(event, SchemaStatusEvent):
            # Preserve existing CLI parity: no generic status lines for schema show/init.
            self._single_table_show = (
                event.operation == "show" and " table '" in event.message
            )
            return

        if isinstance(event, SchemaErrorEvent):
            self._console.print(MessagePanel(event.message, variant="error"))
            return

        if not isinstance(event, SchemaCompleteEvent):
            return

        if (
            event.operation == "init"
            and event.init_result
            and event.init_result.success
        ):
            summary = {
                "Tables": event.init_result.tables,
                "Columns": event.init_result.columns,
                "Relationships": event.init_result.relationships,
            }
            if event.init_result.enum_columns:
                summary["Potential enums"] = len(event.init_result.enum_columns)
            self._console.print(
                MessagePanel("Semantic layer initialized", variant="success")
            )
            self._console.print(KeyValueTable(summary))
            target_name = event.init_result.target
            steps = [
                (
                    f"rdst schema annotate --target {target_name} --use-llm",
                    "AI-generate descriptions",
                ),
                (
                    f"rdst schema edit --target {target_name}",
                    "Manual editing in $EDITOR",
                ),
                (
                    f'rdst ask "How many rows in each table?" --target {target_name}',
                    "Try natural language queries",
                ),
            ]
            self._console.print(NextSteps(steps))
            return

        if event.operation == "show" and event.details:
            self._render_schema_details(event.details)

    def _render_schema_details(self, details: SchemaDetails) -> None:
        self._console.print()
        self._console.print(
            Rule(
                f"Semantic Layer: [{StyleTokens.HEADER}]{details.target}[/{StyleTokens.HEADER}]"
            )
        )

        if details.tables:
            self._console.print(
                f"\n[{StyleTokens.HEADER}]Tables:[/{StyleTokens.HEADER}]"
            )
            tree = SimpleTree(f"[bold]Tables ({len(details.tables)})[/bold]")

            for table in details.tables:
                table_node = tree.add(
                    f"[{StyleTokens.SECONDARY}]{table.name}[/{StyleTokens.SECONDARY}]"
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
                    for col in table.columns:
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
                            f"[{StyleTokens.SECONDARY}]{col.name}[/{StyleTokens.SECONDARY}]{type_str}: {col_desc}"
                        )
                        if col.enum_values:
                            enum_node = col_node.add(
                                f"[{StyleTokens.ACCENT}]Enum values:[/{StyleTokens.ACCENT}]"
                            )
                            for val, meaning in col.enum_values.items():
                                enum_node.add(f"{val} = {meaning}")
                        if col.value_pattern:
                            col_node.add(
                                f"[{StyleTokens.ACCENT}]Pattern: {col.value_pattern}[/{StyleTokens.ACCENT}]"
                            )

            self._console.print(tree)

        if not self._single_table_show:
            if details.extensions:
                self._console.print()
                self._console.print(
                    f"[{StyleTokens.HEADER}]Extensions:[/{StyleTokens.HEADER}]"
                )
                ext_tree = SimpleTree(
                    f"[bold]Installed Extensions ({len(details.extensions)})[/bold]"
                )
                for ext in details.extensions:
                    if ext.description:
                        ext_node = ext_tree.add(
                            f"[{StyleTokens.INFO}]{ext.name}[/{StyleTokens.INFO}] v{ext.version}: {ext.description}"
                        )
                    else:
                        ext_node = ext_tree.add(
                            f"[{StyleTokens.INFO}]{ext.name}[/{StyleTokens.INFO}] v{ext.version}"
                        )
                    if ext.types_provided:
                        ext_node.add(
                            f"[{StyleTokens.MUTED}]Types:[/{StyleTokens.MUTED}] {', '.join(ext.types_provided)}"
                        )
                self._console.print(ext_tree)

            if details.custom_types:
                self._console.print()
                self._console.print(
                    f"[{StyleTokens.HEADER}]Custom Types:[/{StyleTokens.HEADER}]"
                )
                types_tree = SimpleTree(
                    f"[bold]Custom Types ({len(details.custom_types)})[/bold]"
                )
                for custom_type in details.custom_types:
                    if custom_type.type_category == "enum" and custom_type.enum_values:
                        values_preview = ", ".join(custom_type.enum_values[:5])
                        if len(custom_type.enum_values) > 5:
                            values_preview += (
                                f"... ({len(custom_type.enum_values)} total)"
                            )
                        types_tree.add(
                            f"[{StyleTokens.ACCENT}]{custom_type.name}[/{StyleTokens.ACCENT}] [{StyleTokens.MUTED}](enum)[/{StyleTokens.MUTED}]: [{values_preview}]"
                        )
                    elif (
                        custom_type.type_category == "domain" and custom_type.base_type
                    ):
                        types_tree.add(
                            f"[{StyleTokens.ACCENT}]{custom_type.name}[/{StyleTokens.ACCENT}] [{StyleTokens.MUTED}](domain over {custom_type.base_type})[/{StyleTokens.MUTED}]"
                        )
                    elif custom_type.type_category == "base":
                        desc = custom_type.description or "extension type"
                        types_tree.add(
                            f"[{StyleTokens.ACCENT}]{custom_type.name}[/{StyleTokens.ACCENT}] [{StyleTokens.MUTED}]({desc})[/{StyleTokens.MUTED}]"
                        )
                    else:
                        types_tree.add(
                            f"[{StyleTokens.ACCENT}]{custom_type.name}[/{StyleTokens.ACCENT}] [{StyleTokens.MUTED}]({custom_type.type_category})[/{StyleTokens.MUTED}]"
                        )
                self._console.print(types_tree)

            if details.terminology:
                self._console.print()
                self._console.print(
                    f"[{StyleTokens.HEADER}]Terminology:[/{StyleTokens.HEADER}]"
                )
                term_tree = SimpleTree(
                    f"[bold]Business Terms ({len(details.terminology)})[/bold]"
                )
                for term in details.terminology:
                    term_node = term_tree.add(
                        f"[{StyleTokens.SUCCESS}]{term.term}[/{StyleTokens.SUCCESS}]"
                    )
                    term_node.add(
                        f"[{StyleTokens.SUCCESS}]Definition:[/{StyleTokens.SUCCESS}] {term.definition}"
                    )
                    term_node.add(
                        f"[{StyleTokens.SECONDARY}]SQL:[/{StyleTokens.SECONDARY}] {term.sql_pattern}"
                    )
                    if term.synonyms:
                        term_node.add(
                            f"[{StyleTokens.MUTED}]Synonyms:[/{StyleTokens.MUTED}] {', '.join(term.synonyms)}"
                        )
                self._console.print(term_tree)

            if details.metrics:
                self._console.print()
                self._console.print(
                    f"[{StyleTokens.HEADER}]Metrics:[/{StyleTokens.HEADER}]"
                )
                metrics_tree = SimpleTree(
                    f"[bold]Metrics ({len(details.metrics)})[/bold]"
                )
                for metric in details.metrics:
                    metric_node = metrics_tree.add(
                        f"[{StyleTokens.ACCENT}]{metric.name}[/{StyleTokens.ACCENT}]"
                    )
                    metric_node.add(
                        f"[{StyleTokens.SUCCESS}]Definition:[/{StyleTokens.SUCCESS}] {metric.definition}"
                    )
                    metric_node.add(
                        f"[{StyleTokens.SECONDARY}]SQL:[/{StyleTokens.SECONDARY}] {metric.sql}"
                    )
                self._console.print(metrics_tree)

        self._console.print()
        self._console.print(Rule())

    def cleanup(self) -> None:
        return None
