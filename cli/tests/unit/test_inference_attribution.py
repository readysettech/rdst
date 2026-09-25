import ast
from pathlib import Path

from shared.llm_manager.inference_attribution import (
    attribution_for,
    count_billable_calls,
    inference_workflow,
    record_billable_call,
)


def test_workflow_groups_calls_while_operations_remain_distinct():
    with inference_workflow(
        "ask", "cli", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    ):
        clarification = attribution_for("clarification")
        generation = attribution_for("sql_generation")

    assert clarification["workflow_id"] == generation["workflow_id"]
    assert clarification["feature"] == generation["feature"] == "ask"
    assert clarification["surface"] == generation["surface"] == "cli"
    assert clarification["operation"] == "clarification"
    assert generation["operation"] == "sql_generation"


def test_every_production_feature_llm_call_has_a_purpose():
    rdst = Path(__file__).parents[2]
    missing = []
    roots = (rdst / "features", rdst / "shared")
    for root in roots:
        for path in root.rglob("*.py"):
            if path.name == "llm_manager.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"query", "generate_response"}
                ):
                    keywords = {keyword.arg for keyword in node.keywords}
                    is_llm_call = (
                        node.func.attr == "generate_response"
                        or "system_message" in keywords
                    )
                    if is_llm_call and "purpose" not in keywords:
                        missing.append(f"{path.relative_to(rdst)}:{node.lineno}")
    assert missing == []


def test_unscoped_calls_default_to_the_process_surface(monkeypatch):
    monkeypatch.delenv("RDST_DESKTOP", raising=False)
    assert attribution_for("analyze_query")["surface"] == "cli"

    monkeypatch.setenv("RDST_DESKTOP", "1")
    assert attribution_for("analyze_query")["surface"] == "desktop"


def test_monitor_override_is_scoped_and_interactive_analyze_keeps_its_operation():
    with inference_workflow("monitor", "web", operation="monitor_analyze"):
        for purpose in ("analyze_query", "analyze_index", "analyze_cache"):
            assert attribution_for(purpose)["operation"] == "monitor_analyze"
            assert attribution_for(purpose)["feature"] == "monitor"
        assert attribution_for("analyze_query")["surface"] == "web"
    with inference_workflow("analyze", "web"):
        assert attribution_for("analyze_query")["operation"] == "analyze_query"
        assert attribution_for("analyze_query")["feature"] == "analyze"


def test_workflow_threads_preserve_attribution_and_billable_count():
    from shared.workflow_manager_runtime import WorkflowManager

    observed = []

    def call(**kwargs):
        observed.append(attribution_for("analyze_query"))
        record_billable_call()
        return {}

    workflow = {"StartAt": "Call", "States": {"Call": {
        "Type": "Task", "Resource": "call", "TimeoutSeconds": 5, "End": True}}}
    manager = WorkflowManager.from_dict(workflow, resources={"call": call})
    with count_billable_calls() as calls, inference_workflow(
        "monitor", "web", operation="monitor_analyze"
    ):
        run_id = manager.run_async()
        manager.get_workflow_status(run_id).thread.join(timeout=2)
    assert len(observed) == 1
    assert observed[0]["operation"] == "monitor_analyze"
    assert calls.count == 1
