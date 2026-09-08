"""Bind counted entities separately from event occurrences, with question evidence."""

import copy, hashlib, json
from time import perf_counter
from features.ask.matched_percentage import (
    RESPONSE_SCHEMA as OLD_SCHEMA,
    SYSTEM as OLD_SYSTEM,
    CATALOG as OLD_CATALOG,
    accepts_request,
)

VERSION = "percentage-counted-entity-and-context-v1"
ROLE_SCHEMA = {
    "type": "object",
    "properties": {"table": {"type": "string"}, "quote": {"type": "string"}},
    "required": ["table", "quote"],
    "additionalProperties": False,
}
RESPONSE_SCHEMA = copy.deepcopy(OLD_SCHEMA)
del RESPONSE_SCHEMA["properties"]["denominator_table"]
RESPONSE_SCHEMA["properties"].update(
    counted_entity=ROLE_SCHEMA, event_context=ROLE_SCHEMA
)
RESPONSE_SCHEMA["required"] = [
    k for k in OLD_SCHEMA["required"] if k != "denominator_table"
] + ["counted_entity", "event_context"]
SYSTEM = (
    OLD_SYSTEM
    + " Separate the counted entity and the surrounding event context before expressing filters. Return only the populated JSON object, with no Markdown or explanatory text."
)
CATALOG = (
    OLD_CATALOG.replace("denominator_table", "counted_entity.table")
    + """
Role evidence: counted_entity identifies the noun/entity whose percentage is requested; its quote must copy the exact question words naming that counted population. event_context identifies the surrounding event table whose specified occurrences associate those entities; its quote must copy the exact question words identifying that event context. The event context determines repetitions and restrictions, not the identity of the counted entity. Keep counted_entity.table and event_context.table separate even when counting one occurrence of the entity per event. Do not assign an event-context noun to counted_entity merely because repeated entities are weighted by event rows. For event percentages, counted_entity is instead the event table itself. Use empty table/quote for an absent or unidentifiable role, and do not invent one. Explicit distinct/unique counting still requires distinct_entities. Both quotes must be verbatim in the original language, including typos. No generated SQL or proposed transformation is available to infer the roles."""
)


def accepts_percentage_roles(q, plan, request):
    if not isinstance(request, dict) or set(request) != set(
        RESPONSE_SCHEMA["required"]
    ):
        return None
    for key, expected in [
        ("counted_entity", "right_source"),
        ("event_context", "left_source"),
    ]:
        role = request.get(key)
        if not isinstance(role, dict) or set(role) != {"table", "quote"}:
            return None
        if (
            not isinstance(role["table"], str)
            or role["table"].casefold() != plan.facts[expected].casefold()
        ):
            return None
        quote = role["quote"]
        if not isinstance(quote, str) or len(quote.strip()) < 2 or quote not in q:
            return None
    compatible = {
        k: v for k, v in request.items() if k not in ("counted_entity", "event_context")
    }
    compatible["denominator_table"] = request["counted_entity"]["table"]
    return compatible if accepts_request(plan, compatible) else None


def route_percentage_roles(question, plan, adapter, callback=None):
    prompt = json.dumps(
        {
            "effective_question": question,
            "complete_structural_schema": plan.facts["complete_structural_schema"],
            "trigger_catalog": CATALOG,
        },
        ensure_ascii=False,
    )
    started = perf_counter()
    response = adapter.generate_response(
        prompt=prompt,
        system_message=SYSTEM,
        purpose="percentage_roles_routing",
        temperature=0.0,
        max_tokens=1800,
        extra={
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "percentage_roles",
                    "strict": True,
                    "schema": RESPONSE_SCHEMA,
                },
            }
        },
    )
    raw = response.get("response", "")
    if callback:
        callback(
            phase="percentage_roles_routing",
            prompt=prompt,
            response=raw,
            tokens=response.get("tokens_used", 0),
            latency_ms=(perf_counter() - started) * 1000,
            model=response.get("model", "unknown"),
        )
    request = json.loads(raw)
    compatible = accepts_percentage_roles(question, plan, request)
    return {
        "role_request": request,
        "request": compatible,
        "request_receipt": {
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "model": response.get("model"),
        },
        "apply": compatible is not None,
    }
