"""Async generator-based LLM schema annotation service."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator, Callable, Optional

from shared.anthropic_env import has_anthropic_api_key

from .semantic_layer import create_ai_annotator, create_semantic_layer_manager

from .events import (
    AnnotateCompleteEvent,
    AnnotateErrorEvent,
    AnnotateEvent,
    AnnotateProgressEvent,
    AnnotateStartedEvent,
    AnnotateTableCompleteEvent,
)


class AnnotateService:
    """Service for LLM-powered schema annotation with async event streaming."""

    async def annotate(
        self,
        target: str,
        target_config: dict[str, Any],
        table_name: Optional[str] = None,
        sample_rows: int = 5,
    ) -> AsyncGenerator[AnnotateEvent, None]:
        if not has_anthropic_api_key():
            yield AnnotateErrorEvent(
                type="annotate_error",
                message="Anthropic API key not set (ANTHROPIC_API_KEY or RDST_TRIAL_TOKEN). Run 'rdst init' to configure.",
            )
            return

        manager = create_semantic_layer_manager()
        if not manager.exists(target):
            yield AnnotateErrorEvent(
                type="annotate_error",
                message=f"No semantic layer found for '{target}'. Run 'rdst schema init' first.",
            )
            return

        try:
            ai_annotator = create_ai_annotator()
        except Exception as exc:
            yield AnnotateErrorEvent(
                type="annotate_error",
                message=f"Failed to initialize AI annotator: {exc}",
            )
            return

        layer = manager.load(target)
        tables_to_annotate = [table_name] if table_name else list(layer.tables.keys())

        yield AnnotateStartedEvent(
            type="annotate_started",
            tables=len(tables_to_annotate),
            message=f"Starting annotation for {len(tables_to_annotate)} table(s)...",
        )

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

            sample_data = None
            if sample_data_fn:
                try:
                    sample_data = await asyncio.to_thread(sample_data_fn, tbl_name)
                except Exception:
                    pass

            columns_annotated = 0
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
                    pass

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
                        pass

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
        self, target_config: dict[str, Any], sample_rows: int
    ) -> Optional[Callable[[str], list[dict]]]:
        if not target_config:
            return None

        def get_samples(table_name: str) -> list[dict]:
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
