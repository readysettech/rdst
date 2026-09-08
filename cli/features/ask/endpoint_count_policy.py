"""Resolve only count-policy annotations and verbatim count evidence."""

import json, hashlib
from .endpoint_request import accepts_endpoint_request

SCHEMA = {
    "type": "object",
    "properties": {
        "same_count_unit": {"type": "boolean"},
        "verbatim_count_phrase": {"type": "string"},
        "other_conditions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "kind": {
                        "type": "string",
                        "enum": ["once_per_undirected_edge", "unsupported"],
                    },
                },
                "required": ["index", "kind"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["same_count_unit", "verbatim_count_phrase", "other_conditions"],
    "additionalProperties": False,
}
CATALOG = "Resolve only unresolved count-policy annotations and count evidence. The prior extraction must retain its count unit, endpoint number, result shape, and every identified population restriction. For each supplied other condition, decide whether it ONLY says to count each undirected edge once, treating its two reversed storage rows as one edge. That is once_per_undirected_edge. Anything restricting which edges or nodes qualify, including active/approved status, endpoint exclusions, self-loop exclusions, graph selection, dates, weights, thresholds, or added outputs, is unsupported. A policy counting both directions separately, deduplicating parallel edges by endpoint pairs, counting once per graph, or grouping results is also unsupported. Never erase a population condition or reinterpret a missing definition as deduplication. Supply exactly one classification for every supplied index. Copy a count phrase from the original question verbatim, preserving any typos. Indicate whether that phrase supports the already extracted undirected edge count, without changing it. Do not translate or improve spelling. No SQL, data or candidate is supplied."


def can_resolve_count_policy(question, plan, r):
    if (
        not isinstance(r, dict)
        or r.get("status") != "clear"
        or r.get("count_unit") != "undirected_relationships"
        or r.get("result_shape") != "scalar_count"
        or r.get("endpoint_number") != plan.number
    ):
        return False
    c = r.get("conditions")
    if not isinstance(c, list) or not c:
        return False
    if any(
        not isinstance(x, dict)
        or set(x) != {"role", "phrase"}
        or x["role"] not in ["endpoint_number", "all_graphs", "other"]
        or not isinstance(x["phrase"], str)
        or not x["phrase"].strip()
        or x["phrase"] not in question
        for x in c
    ):
        return False
    return sum(x["role"] == "endpoint_number" for x in c) == 1


def apply_count_policy(question, plan, r, policy):
    if (
        not can_resolve_count_policy(question, plan, r)
        or not isinstance(policy, dict)
        or set(policy) != set(SCHEMA["required"])
        or policy["same_count_unit"] is not True
    ):
        return None
    quote = policy["verbatim_count_phrase"]
    if not isinstance(quote, str) or not quote.strip() or quote not in question:
        return None
    wanted = [i for i, c in enumerate(r["conditions"]) if c["role"] == "other"]
    seen = policy["other_conditions"]
    if not isinstance(seen, list) or len(seen) != len(wanted):
        return None
    if any(
        not isinstance(x, dict)
        or set(x) != {"index", "kind"}
        or type(x["index"]) is not int
        or x["kind"] != "once_per_undirected_edge"
        for x in seen
    ):
        return None
    if sorted(x["index"] for x in seen) != wanted:
        return None
    normalized = r | {
        "count_phrase": quote,
        "conditions": [c for c in r["conditions"] if c["role"] != "other"],
    }
    return normalized if accepts_endpoint_request(question, plan, normalized) else None


def resolve_count_policy(question, plan, request, adapter):
    r = request["classification"]
    if request["activate"]:
        return {"activate": True, "status": "prior-contract-accepted", "normalized": r}
    if not can_resolve_count_policy(question, plan, r):
        return {"activate": False, "status": "unsupported-contract"}
    prompt = json.dumps(
        {
            "effective_question": question,
            "trigger_catalog": CATALOG,
            "fixed_count_unit": r["count_unit"],
            "previous_count_phrase": r["count_phrase"],
            "other_conditions": [
                {"index": i, "phrase": c["phrase"]}
                for i, c in enumerate(r["conditions"])
                if c["role"] == "other"
            ],
        },
        ensure_ascii=False,
    )
    response = adapter.generate_response(
        prompt=prompt,
        system_message="Classify count policies and ground a verbatim count phrase only. Preserve every existing population condition and count unit. Return the exact JSON contract.",
        purpose="endpoint_count_policy_routing",
        temperature=0.0,
        max_tokens=1600,
        extra={
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "endpoint_count_policy",
                    "strict": True,
                    "schema": SCHEMA,
                },
            }
        },
    )
    raw = response.get("response", "")
    policy = json.loads(raw)
    normalized = apply_count_policy(question, plan, r, policy)
    return {
        "activate": normalized is not None,
        "status": "resolved" if normalized else "unresolved",
        "normalized": normalized,
        "policy": policy,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "model": response.get("model"),
    }
