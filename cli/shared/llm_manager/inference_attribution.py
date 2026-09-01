"""Bounded attribution metadata for hosted LLM requests."""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
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


_CURRENT_WORKFLOW: ContextVar[InferenceWorkflow | None] = ContextVar(
    "rdst_inference_workflow", default=None
)


@contextmanager
def inference_workflow(
    feature: str,
    surface: str,
    workflow_id: str | None = None,
) -> Iterator[InferenceWorkflow]:
    """Group multiple model calls made for one user-visible action."""
    workflow = InferenceWorkflow(
        feature=feature,
        surface=surface,
        workflow_id=workflow_id or str(uuid.uuid4()),
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
