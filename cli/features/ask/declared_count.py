"""Optional declared-entity count correction at the end of Ask's repair chain."""
import asyncio
from time import perf_counter
import json
import uuid

from .declared_count_language import (
    build_request_arguments, build_binding_arguments, validate_request, validate_binding,
)
from .declared_count_native import apply_count_repair
from .declared_count_planner import plan_count
from .declared_count_schema import count_schema

VERSION = "declared-count-correction-v1"


def _decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate-count-response-key")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("nonfinite-count-response")

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    if type(value) is not dict:
        raise ValueError("count-response-object-required")
    return value


def _call(adapter, ctx, arguments):
    start = perf_counter()
    request = dict(arguments)
    purpose = request.pop("purpose")
    response = adapter.generate_response(purpose=purpose, **request)
    if not isinstance(response, dict):
        raise ValueError("invalid-count-model-response")
    raw = response.get("response", "")
    tokens = response.get("tokens_used", 0)
    ctx.add_llm_call(prompt=arguments["prompt"], response=raw if isinstance(raw, str) else "",
                     tokens=tokens if type(tokens) is int else 0,
                     latency_ms=(perf_counter() - start) * 1000,
                     model=response.get("model", "unknown"), phase=arguments["purpose"])
    return _decode(raw)


async def apply_declared_count(ctx, llm_manager, db_executor=None, *,
                               snapshot_correction, restore_correction):
    original = snapshot_correction(ctx)
    diagnostics = {"version": VERSION, "status": "unchanged", "model_calls_attempted": 0}
    ctx.declared_count = diagnostics
    result = ctx.execution_result
    if result is None or result.error or result.truncated or len(result.rows) != 1:
        diagnostics["reason"] = "complete-scalar-result-unavailable"
        return ctx
    if ctx.provided_context or ctx.conversation_context:
        diagnostics["reason"] = "additional-context-requires-abstention"
        return ctx
    try:
        schema = count_schema(ctx.schema_info, ctx.db_type)
        plan = plan_count(ctx.sql or "", schema, ctx.db_type)
        if not plan["supported"]:
            diagnostics["reason"] = plan["reason"]
            return ctx
        question = ctx.refined_question or ctx.question
        diagnostics["model_calls_attempted"] += 1
        raw_request = await asyncio.to_thread(_call, llm_manager, ctx, build_request_arguments(question))
        request = validate_request(question, raw_request)
        diagnostics["request"] = request
        if not request["valid"] or not request["eligible"]:
            diagnostics["reason"] = request["reason"]
            return ctx
        arguments = build_binding_arguments(question, request["value"], plan["catalog"])
        diagnostics["model_calls_attempted"] += 1
        raw_binding = await asyncio.to_thread(_call, llm_manager, ctx, arguments)
        binding = validate_binding(question, request["value"], raw_binding, plan["catalog"])
        diagnostics["binding"] = binding
        if not binding["valid"] or not binding["eligible"]:
            diagnostics["reason"] = binding["reason"]
            return ctx
        await asyncio.to_thread(apply_count_repair, ctx, schema, request["value"], binding["value"],
            execution_id=str(uuid.uuid4()), snapshot_correction=snapshot_correction,
            restore_correction=restore_correction, db_executor=db_executor)
        diagnostics["native"] = ctx.count_native
        diagnostics["status"] = "normalized" if ctx.count_native["may_adopt"] else "unchanged"
        if not ctx.count_native["may_adopt"]:
            diagnostics["reason"] = ctx.count_native.get("reason", "native-proof-declined")
    except Exception as exc:
        restore_correction(ctx, original)
        diagnostics.update(status="reverted", reason="declared-count-failed", error_kind=type(exc).__name__)
    return ctx
