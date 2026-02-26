"""AnnotateService - Async generator-based LLM schema annotation service.

This service provides streaming LLM annotation for semantic layers,
yielding events as each table/column is processed.
"""

import asyncio
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from .anthropic_env import has_anthropic_api_key
from .types import (
    AnnotateEvent,
    AnnotateStartedEvent,
    AnnotateProgressEvent,
    AnnotateTableCompleteEvent,
    AnnotateCompleteEvent,
    AnnotateErrorEvent,
)


class AnnotateService:
    """Service for LLM-powered schema annotation with async event streaming.

    Usage:
        service = AnnotateService()
        async for event in service.annotate(target, target_config):
            if event.type == "annotate_progress":
                print(f"Annotating {event.table}...")
            elif event.type == "annotate_complete":
                print(f"Done! Annotated {event.tables_annotated} tables")
    """

    def __init__(self) -> None:
        """Initialize the annotate service."""
        pass

    async def annotate(
        self,
        target: str,
        target_config: Dict[str, Any],
        table_name: Optional[str] = None,
        sample_rows: int = 5,
    ) -> AsyncGenerator[AnnotateEvent, None]:
        """Annotate schema with LLM and yield events during execution.

        Args:
            target: Target database name
            target_config: Database connection config (for sample data)
            table_name: Optional specific table to annotate (all if None)
            sample_rows: Number of sample rows for LLM context

        Yields:
            AnnotateEvent instances as annotation progresses
        """
        # Check for API key
        if not has_anthropic_api_key():
            yield AnnotateErrorEvent(
                type="annotate_error",
                message="Anthropic API key not set (ANTHROPIC_API_KEY or RDST_TRIAL_TOKEN). Run 'rdst init' to configure.",
            )
            return

        # Load semantic layer manager
        from ..semantic_layer.manager import SemanticLayerManager

        manager = SemanticLayerManager()

        if not manager.exists(target):
            yield AnnotateErrorEvent(
                type="annotate_error",
                message=f"No semantic layer found for '{target}'. Run 'rdst schema init' first.",
            )
            return

        # Create AI annotator
        try:
            from ..semantic_layer.ai_annotator import AIAnnotator

            ai_annotator = AIAnnotator()
        except Exception as e:
            yield AnnotateErrorEvent(
                type="annotate_error",
                message=f"Failed to initialize AI annotator: {e}",
            )
            return

        # Load the layer
        layer = manager.load(target)

        # Determine tables to annotate
        tables_to_annotate = [table_name] if table_name else list(layer.tables.keys())

        yield AnnotateStartedEvent(
            type="annotate_started",
            tables=len(tables_to_annotate),
            message=f"Starting annotation for {len(tables_to_annotate)} table(s)...",
        )

        # Create sample data function
        sample_data_fn = self._create_sample_data_function(target_config, sample_rows)

        total_tables_annotated = 0
        total_columns_annotated = 0

        for i, tbl_name in enumerate(tables_to_annotate):
            if tbl_name not in layer.tables:
                continue

            table = layer.tables[tbl_name]

            yield AnnotateProgressEvent(
                type="annotate_progress",
                table=tbl_name,
                table_index=i + 1,
                total_tables=len(tables_to_annotate),
                message=f"Annotating {tbl_name}...",
            )

            # Get sample data
            sample_data = None
            if sample_data_fn:
                try:
                    sample_data = await asyncio.to_thread(sample_data_fn, tbl_name)
                except Exception:
                    pass

            columns_annotated = 0

            # Generate table description if missing
            if not table.description:
                try:
                    description = await asyncio.to_thread(
                        ai_annotator.generate_table_description,
                        tbl_name,
                        table.columns,
                        table.row_estimate or "unknown",
                        sample_data,
                        f"{target} database",
                    )
                    if not description.startswith("Error"):
                        table.description = description
                        total_tables_annotated += 1
                except Exception:
                    pass  # Skip failed tables

            # Generate column descriptions
            for col_name, col in table.columns.items():
                if not col.description:
                    try:
                        col_desc = await asyncio.to_thread(
                            ai_annotator.generate_column_description,
                            tbl_name,
                            col_name,
                            col.data_type or "unknown",
                            None,
                            table.description,
                        )
                        if not col_desc.startswith("Error"):
                            col.description = col_desc
                            columns_annotated += 1
                            total_columns_annotated += 1
                    except Exception:
                        pass  # Skip failed columns

            # Save after each table to preserve progress
            manager.save(layer)

            yield AnnotateTableCompleteEvent(
                type="annotate_table_complete",
                table=tbl_name,
                table_index=i + 1,
                total_tables=len(tables_to_annotate),
                columns_annotated=columns_annotated,
            )

        yield AnnotateCompleteEvent(
            type="annotate_complete",
            success=True,
            tables_annotated=total_tables_annotated,
            columns_annotated=total_columns_annotated,
            message=f"Annotated {total_tables_annotated} table(s) and {total_columns_annotated} column(s)",
        )

    def _create_sample_data_function(
        self, target_config: Dict[str, Any], sample_rows: int
    ) -> Optional[Callable[[str], List[Dict]]]:
        """Create a function that fetches sample data from a table."""
        if not target_config:
            return None

        def get_samples(table_name: str) -> List[Dict]:
            try:
                import psycopg2

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
