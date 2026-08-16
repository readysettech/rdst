"""Async generator-based LLM schema annotation service."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from typing import Any, Optional

from shared.anthropic_env import validate_anthropic_key
from shared.db_connection import (
    create_mysql_connection_from_params,
    postgres_connection_kwargs,
    quote_identifier,
    resolve_connection_params,
)

from .events import (
    AnnotateCompleteEvent,
    AnnotateErrorEvent,
    AnnotateEvent,
    AnnotateProgressEvent,
    AnnotateStartedEvent,
    AnnotateTableCompleteEvent,
)
from .semantic_layer import create_ai_annotator, create_semantic_layer_manager


class AnnotateService:
    """Service for LLM-powered schema annotation with async event streaming.

    Each table is annotated with a single batched LLM call (see
    AIAnnotator.annotate_table); tables run in concurrent mini-batches with an
    incremental save after every batch, so an interrupted run keeps its
    completed tables and a rerun skips them.
    """

    CONCURRENT_TABLES = 3

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
        requested = [table_name] if table_name else list(layer.tables.keys())
        all_work = [
            (name, layer.tables[name]) for name in requested if name in layer.tables
        ]
        work = [
            (name, table)
            for name, table in all_work
            if self._table_needs_annotation(table)
        ]
        completed_before = len(all_work) - len(work)
        total_tables = len(all_work)

        if completed_before and work:
            start_message = (
                f"Resuming annotation: {completed_before} of {total_tables} "
                "table(s) already complete"
            )
        elif not work:
            start_message = f"All {total_tables} table(s) already annotated"
        else:
            start_message = f"Starting annotation for {total_tables} table(s)..."

        yield AnnotateStartedEvent(
            type="annotate_started",
            tables=total_tables,
            completed_tables=completed_before,
            message=start_message,
        )

        order_columns_by_table, hash_order_tables = self._sample_order_plan(layer)
        sample_data_fn = self._create_sample_data_function(
            target,
            target_config,
            sample_rows,
            order_columns_by_table=order_columns_by_table,
            hash_order_tables=hash_order_tables,
        )
        total_tables_annotated = 0
        total_columns_annotated = 0
        total_failures = 0
        last_failure: Optional[str] = None

        for start in range(0, len(work), self.CONCURRENT_TABLES):
            batch = work[start : start + self.CONCURRENT_TABLES]
            for offset, (tbl_name, _table) in enumerate(batch):
                yield AnnotateProgressEvent(
                    type="annotate_progress",
                    table=tbl_name,
                    table_index=completed_before + start + offset + 1,
                    total_tables=total_tables,
                    message=f"Annotating {tbl_name}...",
                )

            outcomes = await asyncio.gather(
                *(
                    asyncio.to_thread(
                        self._annotate_table_sync,
                        ai_annotator,
                        tbl_name,
                        table,
                        sample_data_fn,
                        target,
                    )
                    for tbl_name, table in batch
                )
            )

            completed: list[AnnotateTableCompleteEvent] = []
            batch_changed = False
            for offset, ((tbl_name, table), (status, payload)) in enumerate(
                zip(batch, outcomes)
            ):
                columns_added = 0
                if status == "error":
                    total_failures += 1
                    last_failure = payload
                else:
                    tables_added, columns_added, changed = self._apply_result(
                        table, payload
                    )
                    total_tables_annotated += tables_added
                    total_columns_annotated += columns_added
                    batch_changed = batch_changed or changed
                completed.append(
                    AnnotateTableCompleteEvent(
                        type="annotate_table_complete",
                        table=tbl_name,
                        table_index=completed_before + start + offset + 1,
                        total_tables=total_tables,
                        columns_annotated=columns_added,
                    )
                )

            if batch_changed:
                await asyncio.to_thread(manager.save, layer)

            for event in completed:
                yield event

        # Zero annotations despite failed attempts is a failure, not a green
        # complete; the false-success the user hit (rdst-0yy.11). A no-op run
        # (everything already annotated, no failures) still completes honestly.
        if total_tables_annotated + total_columns_annotated == 0 and total_failures > 0:
            message = f"Annotated 0 of {len(work)} table(s): every AI request failed."
            if last_failure:
                message += f" Last error: {self._one_line(last_failure)}"
            yield AnnotateErrorEvent(type="annotate_error", message=message)
            return

        yield AnnotateCompleteEvent(
            type="annotate_complete",
            success=total_failures == 0,
            tables_annotated=total_tables_annotated,
            columns_annotated=total_columns_annotated,
            tables_failed=total_failures,
            message=self._complete_message(
                total_tables_annotated, total_columns_annotated, total_failures
            ),
        )

    def _annotate_table_sync(
        self,
        ai_annotator: Any,
        tbl_name: str,
        table: Any,
        sample_data_fn: Optional[Callable[[str], list[dict]]],
        target: str,
    ) -> tuple[str, Any]:
        """Sample and annotate one table with a single batched LLM call.

        Runs on a worker thread. Returns ("ok", result_dict), ("ok", None)
        when the table needs no annotation, or ("error", message). Only
        columns that still need annotation are requested; a partially
        annotated table does not re-request what it already has. A DB-side
        sampling failure is not an annotation failure; it degrades to no
        samples rather than aborting the table.
        """
        pending = [name for name, col in table.columns.items() if col.needs_annotation]

        sample_data = None
        if sample_data_fn:
            try:
                sample_data = sample_data_fn(tbl_name)
            except Exception:
                sample_data = None

        try:
            result = ai_annotator.annotate_table(
                tbl_name,
                table,
                sample_data,
                f"{target} database",
                only_columns=pending,
            )
            return ("ok", result)
        except Exception as exc:
            return ("error", str(exc))

    @staticmethod
    def _table_needs_annotation(table: Any) -> bool:
        """Whether a rerun still has useful AI work for this table."""
        return not table.description or any(
            column.needs_annotation for column in table.columns.values()
        )

    @staticmethod
    def _apply_result(table: Any, result: Optional[dict]) -> tuple[int, int, bool]:
        """Apply a batched annotation result to the layer, filling only empty
        fields. Returns (tables_added, columns_added, changed); changed also
        covers business-context and enum-meaning fills, which must reach the
        incremental save even when no description counters moved."""
        if not result:
            return (0, 0, False)

        tables_added = 0
        columns_added = 0
        changed = False
        if not table.description and result.get("description"):
            table.description = result["description"]
            tables_added = 1
        if not table.business_context and result.get("business_context"):
            table.business_context = result["business_context"]
            changed = True

        result_columns = result.get("columns") or {}
        for col_name, col in table.columns.items():
            data = result_columns.get(col_name)
            if not isinstance(data, dict):
                continue
            if not col.description and data.get("description"):
                col.description = data["description"]
                columns_added += 1
            mappings = data.get("enum_mappings") or {}
            for value in col.missing_enum_meanings():
                meaning = mappings.get(value)
                if meaning:
                    col.enum_values[value] = meaning
                    changed = True

        changed = changed or bool(tables_added or columns_added)
        return tables_added, columns_added, changed

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
        if tables == 0 and columns == 0 and failures == 0:
            return "Schema is already fully annotated"
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
        self,
        target: str,
        target_config: dict[str, Any],
        sample_rows: int,
        *,
        order_columns_by_table: dict[str, list[str]],
        hash_order_tables: set[str] | None = None,
        raise_sample_errors: bool = False,
    ) -> Optional[Callable[[str], list[dict]]]:
        if not target_config:
            return None

        def get_samples(table_name: str) -> list[dict]:
            conn = None
            try:
                params = resolve_connection_params(
                    target=target,
                    target_config=target_config,
                )
                engine = params["engine"]
                if engine == "mysql":
                    conn = create_mysql_connection_from_params(params)
                else:
                    import psycopg2

                    conn = psycopg2.connect(**postgres_connection_kwargs(params))
                cursor = conn.cursor()
                order_columns = order_columns_by_table.get(table_name, [])
                if not order_columns:
                    return []
                quoted_columns = [
                    quote_identifier(column, engine).replace("%", "%%")
                    for column in order_columns
                ]
                if table_name in (hash_order_tables or set()):
                    order_clause = self._row_hash_order_clause(quoted_columns, engine)
                else:
                    order_clause = ", ".join(quoted_columns)
                quoted_table = quote_identifier(table_name, engine).replace("%", "%%")
                cursor.execute(
                    f"SELECT * FROM {quoted_table} ORDER BY {order_clause} LIMIT %s",
                    (sample_rows,),
                )
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
                return [dict(zip(columns, row)) for row in rows]
            except Exception as exc:
                if raise_sample_errors:
                    raise RuntimeError(
                        f"Failed to sample table {table_name!r}: {exc}"
                    ) from exc
                return []
            finally:
                if conn is not None:
                    conn.close()

        return get_samples

    @staticmethod
    def _row_hash_order_clause(quoted_columns: list[str], engine: str) -> str:
        """Build a fixed-width deterministic sort expression for keyless rows."""
        if engine == "mysql":
            serialized_columns = ", ".join(
                f"COALESCE(CAST({column} AS CHAR), CHAR(30))"
                for column in quoted_columns
            )
            return f"SHA2(CONCAT_WS(CHAR(31), {serialized_columns}), 256)"
        serialized_columns = ", ".join(
            f"COALESCE(CAST({column} AS TEXT), CHR(30))" for column in quoted_columns
        )
        return f"MD5(CONCAT_WS(CHR(31), {serialized_columns}))"

    @staticmethod
    def _sample_order_columns(layer) -> dict[str, list[str]]:
        """Return columns used by the deterministic sample ordering plan."""
        return AnnotateService._sample_order_plan(layer)[0]

    @staticmethod
    def _sample_order_plan(layer) -> tuple[dict[str, list[str]], set[str]]:
        """Prefer primary keys; hash complete rows for keyless tables.

        Ordering a wide table by every column can exhaust the database sort
        buffer.  A fixed-width row digest preserves deterministic sampling
        without making the sort key proportional to the row width.
        """
        result = {}
        hash_order_tables = set()
        for table_name, table in layer.tables.items():
            primary_keys = next(
                (
                    list(index.columns)
                    for _name, index in sorted(table.indexes.items())
                    if index.is_primary and index.columns
                ),
                [],
            )
            result[table_name] = primary_keys or list(table.columns)
            if not primary_keys:
                hash_order_tables.add(table_name)
        return result, hash_order_tables
