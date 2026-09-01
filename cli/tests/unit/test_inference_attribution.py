import ast
from pathlib import Path

from shared.llm_manager.inference_attribution import (
    attribution_for,
    inference_workflow,
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
