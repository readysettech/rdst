"""Async generator-based LLM schema annotation service."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator, Callable, Optional

from shared.anthropic_env import validate_anthropic_key

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
        # Validity, not presence: a present-but-rejected key would otherwise
        # march every table and report a false success (rdst-0yy.11). The ping
        # is blocking, so offload it off the event loop.
        validity = await asyncio.to_thread(validate_anthropic_key)
        if not validity["valid"]:
            yield AnnotateErrorEvent(
                type="annotate_error",
                message=self._key_error_message(validity["reason"]),
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
        total_failures = 0
        last_failure: Optional[str] = None

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

            sample_data = await self._sample_data(sample_data_fn, tbl_name)
            tables_added, columns_added, failures, failure = await self._annotate_table(
                ai_annotator, tbl_name, table, sample_data, target
            )
            total_tables_annotated += tables_added
            total_columns_annotated += columns_added
            total_failures += failures
            if failure is not None:
                last_failure = failure

            manager.save(layer)

            yield AnnotateTableCompleteEvent(
                type="annotate_table_complete",
                table=tbl_name,
                table_index=i + 1,
                total_tables=len(tables_to_annotate),
                columns_annotated=columns_added,
            )

        # Zero annotations despite failed attempts is a failure, not a green
        # complete; the false-success the user hit (rdst-0yy.11). A no-op run
        # (everything already annotated, no failures) still completes honestly.
        if total_tables_annotated + total_columns_annotated == 0 and total_failures > 0:
            message = (
                f"Annotated 0 of {len(tables_to_annotate)} table(s): every AI "
                "request failed."
            )
            if last_failure:
                message += f" Last error: {self._one_line(last_failure)}"
            yield AnnotateErrorEvent(type="annotate_error", message=message)
            return

        yield AnnotateCompleteEvent(
            type="annotate_complete",
            success=True,
            tables_annotated=total_tables_annotated,
            columns_annotated=total_columns_annotated,
            message=self._complete_message(
                total_tables_annotated, total_columns_annotated, total_failures
            ),
        )

    async def _sample_data(
        self,
        sample_data_fn: Optional[Callable[[str], list[dict]]],
        tbl_name: str,
    ) -> Optional[list[dict]]:
        """Best-effort row sample; a DB-side failure is not an annotation
        failure, so it degrades to no samples rather than aborting the run."""
        if not sample_data_fn:
            return None
        try:
            return await asyncio.to_thread(sample_data_fn, tbl_name)
        except Exception:
            return None

    async def _annotate_table(
        self,
        ai_annotator: Any,
        tbl_name: str,
        table: Any,
        sample_data: Optional[list[dict]],
        target: str,
    ) -> tuple[int, int, int, Optional[str]]:
        """Annotate one table's description and columns.

        Returns (tables_added, columns_added, failures, last_failure). A failure
        is the annotator throwing or returning an 'Error...' string; these are
        counted rather than swallowed, so the caller can refuse a false success.
        """
        tables_added = 0
        columns_added = 0
        failures = 0
        last_failure: Optional[str] = None

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
                if description.startswith("Error"):
                    failures += 1
                    last_failure = description
                else:
                    table.description = description
                    tables_added = 1
            except Exception as exc:
                failures += 1
                last_failure = str(exc)

        for col_name, col in table.columns.items():
            if col.description:
                continue
            try:
                col_desc = await asyncio.to_thread(
                    ai_annotator.generate_column_description,
                    tbl_name,
                    col_name,
                    col.data_type or "unknown",
                    None,
                    table.description,
                )
                if col_desc.startswith("Error"):
                    failures += 1
                    last_failure = col_desc
                else:
                    col.description = col_desc
                    columns_added += 1
            except Exception as exc:
                failures += 1
                last_failure = str(exc)

        return tables_added, columns_added, failures, last_failure

    @staticmethod
    def _key_error_message(reason: str) -> str:
        """Honest, reason-specific preflight message for an unusable key."""
        if reason == "no_key":
            return (
                "Anthropic API key not set (ANTHROPIC_API_KEY or "
                "RDST_TRIAL_TOKEN). Run 'rdst init' to configure."
            )
        if reason == "rejected":
            return (
                "Anthropic rejected the configured key. Update it with a valid "
                "key in Configure, then run AI Annotate again."
            )
        return (
            "Could not reach Anthropic to verify the key. Check your connection "
            "and try again."
        )

    @staticmethod
    def _complete_message(tables: int, columns: int, failures: int) -> str:
        message = f"Annotated {tables} table(s) and {columns} column(s)"
        if failures:
            message += f" ({failures} AI request(s) failed)"
        return message

    @staticmethod
    def _one_line(text: str, limit: int = 160) -> str:
        collapsed = " ".join(text.split())
        if len(collapsed) <= limit:
            return collapsed
        return collapsed[:limit] + "..."

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
