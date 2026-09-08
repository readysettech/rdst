import json

from features.ask.ambiguity_detection import detect_ambiguities
from features.schema.inference_context import schema_context_prefix
from features.ask.sql_generation import generate_sql_from_nl
from shared.llm_manager.base import ProviderRequest
from shared.llm_manager.hosted_glm_provider import HostedGLMProvider


def test_schema_prefix_is_stable_and_changes_with_schema_or_engine():
    schema = "Table: items\n  id (integer)"
    prefix = schema_context_prefix(schema, "postgresql")
    assert prefix == schema_context_prefix(schema, "postgresql")
    assert prefix != schema_context_prefix(schema + "\n  price (numeric)", "postgresql")
    assert prefix != schema_context_prefix(schema, "mysql")
    assert "untrusted data" in prefix


def test_clarification_and_generation_share_hosted_schema_prefix():
    schema = "Table: items (id integer)"
    requests = []

    class Manager:
        def generate_response(self, **kwargs):
            requests.append(kwargs)
            if kwargs["purpose"] == "clarification":
                response = {
                    "ambiguities": [], "total_ambiguities": 0,
                    "requires_clarification": False,
                    "can_proceed_with_assumptions": True,
                    "overall_confidence": 1.0,
                }
            else:
                response = {
                    "sql": "SELECT COUNT(*) FROM items",
                    "explanation": "Count items.", "confidence": 1.0,
                    "assumptions": [], "cannot_answer": False,
                    "cannot_answer_reason": "", "missing_schema": [],
                }
            return {"response": json.dumps(response), "tokens_used": 10}

    manager = Manager()
    assert detect_ambiguities(
        "Count items", schema, "postgresql", manager
    )["success"]
    assert generate_sql_from_nl(
        "Count all items", schema, "postgresql", "fixture", manager
    )["success"]
    prefix = schema_context_prefix(schema, "postgresql")
    for request in requests:
        messages = HostedGLMProvider._messages(ProviderRequest(
            model="fixture",
            messages=[
                {"role": "system", "content": request["system_message"]},
                {"role": "user", "content": request["prompt"]},
            ],
            max_tokens=1000, temperature=0, extra=request["extra"],
        ))
        assert messages[0]["content"].startswith(prefix)
        assert messages[0]["content"].count(schema) == 1
        assert schema not in messages[1]["content"]
        assert messages[0]["content"].index("JSON Schema") >= len(prefix)
        assert "Count items" not in prefix
        assert "Count all items" not in prefix
