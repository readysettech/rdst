"""Bounded attribution metadata for hosted LLM requests."""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Lock
from typing import Iterator


PURPOSE_FEATURES = {
    "anthropic_key_validation": "init",
    "clarification": "ask",
    "correction_intent_routing": "ask",
    "example_questions": "ask",
    "sql_generation": "ask",
    "sql_generation_alternate": "ask",
    "sql_recovery": "ask",
    "sql_refinement": "ask",
    "sql_validation_repair": "ask",
    "analyze_cache": "analyze",
    "analyze_index": "analyze",
    "analyze_query": "analyze",
    "analyze_rewrite": "analyze",
    "audit_benchmark": "audit",
    "audit_capture_analysis": "audit",
    "audit_health": "audit",
    "monitor_report": "monitor",
    "monitor_analyze": "monitor",
    "audit_single_target_insights": "audit",
    "fleet_insights": "fleet",
    "guard_rule_derivation": "guard",
    "live_compare": "cache",
    "speed_test": "cache",
    "init_connectivity_check": "init",
    "interactive_answer": "interactive",
    "help_answer": "help",
    "orm_conversion": "scan",
    "query_registry_analysis": "queries",
    "schema_annotation": "schema",
    "schema_column_annotation": "schema",
    "schema_enum_annotation": "schema",
    "schema_guided_analysis": "schema",
    "schema_table_annotation": "schema",
    "test_data_generation": "test_data",
    "workflow_llm": "workflow",
}


@dataclass(frozen=True)
class InferenceWorkflow:
    feature: str
    surface: str
    workflow_id: str
    operation: str | None = None


class BillableCalls:
    def __init__(self) -> None:
        self.count = 0
        self._lock = Lock()

    def record(self) -> None:
        with self._lock:
            self.count += 1


_CURRENT_WORKFLOW: ContextVar[InferenceWorkflow | None] = ContextVar(
    "rdst_inference_workflow", default=None
)
_BILLABLE_CALLS: ContextVar[BillableCalls | None] = ContextVar(
    "rdst_billable_calls", default=None
)
_MONITOR_DATABASE: ContextVar[str | None] = ContextVar(
    "rdst_monitor_database", default=None
)
MONITOR_DATABASE_HEADER = "X-RDST-Monitor-Database"


@contextmanager
def monitoring_database(fingerprint: str | None) -> Iterator[None]:
    """Mark Keyservice calls in this block as monitoring work for one database."""
    token = _MONITOR_DATABASE.set(fingerprint)
    try:
        yield
    finally:
        _MONITOR_DATABASE.reset(token)


def monitor_database_headers() -> dict[str, str]:
    """The header that counts this call toward the account's monitored-database limit."""
    fingerprint = _MONITOR_DATABASE.get()
    return {MONITOR_DATABASE_HEADER: fingerprint} if fingerprint else {}


@contextmanager
def count_billable_calls() -> Iterator[BillableCalls]:
    calls = BillableCalls()
    token = _BILLABLE_CALLS.set(calls)
    try:
        yield calls
    finally:
        _BILLABLE_CALLS.reset(token)


def record_billable_call() -> None:
    """Record one successful, billable Keyservice response in the current run."""
    calls = _BILLABLE_CALLS.get()
    if calls is not None:
        calls.record()


@contextmanager
def inference_workflow(
    feature: str,
    surface: str,
    workflow_id: str | None = None,
    operation: str | None = None,
) -> Iterator[InferenceWorkflow]:
    """Group calls for one action and optionally assign one bounded operation."""
    workflow = InferenceWorkflow(
        feature=feature,
        surface=surface,
        workflow_id=workflow_id or str(uuid.uuid4()),
        operation=operation,
    )
    token = _CURRENT_WORKFLOW.set(workflow)
    try:
        yield workflow
    finally:
        _CURRENT_WORKFLOW.reset(token)


def attribution_for(purpose: str | None) -> dict[str, str]:
    """Return the bounded metadata sent only to Readyset Keyservice."""
    operation = str(purpose or "other")
    feature = PURPOSE_FEATURES.get(operation, "other")
    workflow = _CURRENT_WORKFLOW.get()
    if workflow is not None:
        feature = workflow.feature
        if workflow.operation is not None:
            operation = workflow.operation
        workflow_id = workflow.workflow_id
        surface = workflow.surface
    else:
        workflow_id = str(uuid.uuid4())
        surface = "desktop" if os.getenv("RDST_DESKTOP") == "1" else "cli"
    return {
        "feature": feature,
        "operation": operation if operation in PURPOSE_FEATURES else "other",
        "surface": surface,
        "workflow_id": workflow_id,
    }
